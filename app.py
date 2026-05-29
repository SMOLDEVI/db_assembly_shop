from flask import Flask, render_template, request, redirect, url_for, flash, Response, session
import sqlite3
import csv
import io
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = 'super_secret_key' # Обязательно для работы сессий и flash-уведомлений

def get_db_connection():
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    return conn

# Декоратор для ограничения доступа по авторизации и ролям
def login_required(role=None):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                flash('Для доступа к этой странице необходимо войти в систему.', 'warning')
                return redirect(url_for('login'))
            if role and session.get('role') != role:
                flash('У вас нет прав для доступа к этой странице.', 'danger')
                return redirect(url_for('index'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# Помощник для получения плана из сессии или создания дефолтного плана
def get_production_plans(conn):
    if 'production_plans' not in session or not session['production_plans']:
        products = conn.execute('SELECT id_product_type FROM product_type').fetchall()
        default_plans = {}
        defaults = {1: 10, 2: 5, 3: 2, 4: 1, 5: 3}
        for p in products:
            p_id = p['id_product_type']
            default_plans[str(p_id)] = defaults.get(p_id, 2)
        session['production_plans'] = default_plans
        session.modified = True
    return session['production_plans']

# ================= АВТОРИЗАЦИЯ И РЕГИСТРАЦИЯ =================
@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('index'))
        
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password'].strip()
        
        conn = get_db_connection()
        user = conn.execute('SELECT * FROM user WHERE username = ?', (username,)).fetchone()
        conn.close()
        
        if user and check_password_hash(user['password_hash'], password):
            session['user_id'] = user['id_user']
            session['username'] = user['username']
            session['role'] = user['role']
            flash(f'Добро пожаловать, {user["username"]}!', 'success')
            return redirect(url_for('index'))
        else:
            flash('Неверное имя пользователя или пароль!', 'danger')
            
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'user_id' in session:
        return redirect(url_for('index'))
        
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password'].strip()
        role = request.form['role'].strip()
        
        if not username or not password or role not in ['worker', 'client']:
            flash('Заполните все поля корректно!', 'danger')
        else:
            conn = get_db_connection()
            existing = conn.execute('SELECT id_user FROM user WHERE username = ?', (username,)).fetchone()
            if existing:
                flash('Пользователь с таким именем уже существует!', 'danger')
                conn.close()
            else:
                pw_hash = generate_password_hash(password)
                conn.execute('INSERT INTO user (username, password_hash, role) VALUES (?, ?, ?)', 
                             (username, pw_hash, role))
                conn.commit()
                conn.close()
                flash('Регистрация прошла успешно! Теперь вы можете войти в систему.', 'success')
                return redirect(url_for('login'))
                
    return render_template('register.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Вы успешно вышли из системы.', 'info')
    return redirect(url_for('login'))

# ================= 1. ГЛАВНАЯ (ПАНЕЛЬ ДЛЯ РАБОТНИКА / КАТАЛОГ ДЛЯ КЛИЕНТА) =================
@app.route('/')
@login_required()
def index():
    # Если зашел Клиент — показываем ему каталог и его заказы
    if session.get('role') == 'client':
        conn = get_db_connection()
        products = conn.execute('SELECT * FROM product_type').fetchall()
        
        # Получаем заказы конкретного пользователя
        my_orders = conn.execute('''
            SELECT co.*, pt.name as product_name
            FROM client_order co
            JOIN product_type pt ON co.product_type_id = pt.id_product_type
            WHERE co.user_id = ?
            ORDER BY co.id_order DESC
        ''', (session['user_id'],)).fetchall()
        conn.close()
        return render_template('client_dashboard.html', products=products, orders=my_orders)
        
    # Если зашел Работник — показываем стандартный аналитический дашборд
    conn = get_db_connection()
    items = conn.execute('''
        SELECT pt.name, SUM(pwc.quantity_stored) as total 
        FROM product_warehouse_cell pwc
        JOIN product_type pt ON pwc.product_type_id = pt.id_product_type
        GROUP BY pt.id_product_type HAVING total > 0
    ''').fetchall()
    
    products = conn.execute('SELECT * FROM product_type').fetchall()
    plans = get_production_plans(conn)
    deficit_count = 0
    
    parts = conn.execute('SELECT id_part FROM part').fetchall()
    for part in parts:
        part_id = part['id_part']
        total_needed = 0
        for p in products:
            p_id_str = str(p['id_product_type'])
            plan_qty = plans.get(p_id_str, 0)
            if plan_qty > 0:
                comp = conn.execute('SELECT quantity FROM product_composition WHERE product_type_id = ? AND part_id = ?', 
                                    (p['id_product_type'], part_id)).fetchone()
                if comp:
                    total_needed += plan_qty * comp['quantity']
        
        stock_row = conn.execute('SELECT SUM(quantity_stored) as total FROM part_warehouse_cell WHERE part_id = ?', (part_id,)).fetchone()
        in_stock = stock_row['total'] if stock_row['total'] else 0
        if total_needed > in_stock:
            deficit_count += 1

    stats = {
        'total_products': conn.execute('SELECT SUM(quantity_stored) FROM product_warehouse_cell').fetchone()[0] or 0,
        'total_parts': conn.execute('SELECT SUM(quantity_stored) FROM part_warehouse_cell').fetchone()[0] or 0,
        'total_configs': conn.execute('SELECT COUNT(DISTINCT product_type_id) FROM product_composition').fetchone()[0] or 0,
        'deficit_count': deficit_count
    }
    conn.close()
    
    labels = [item['name'] for item in items]
    data = [item['total'] for item in items]
    return render_template('index.html', labels=labels, data=data, stats=stats)

# ================= КЛИЕНТСКИЙ ФУНКЦИОНАЛ: ОФОРМЛЕНИЕ ЗАКАЗА =================
@app.route('/client_order', methods=['POST'])
@login_required('client')
def place_client_order():
    product_type_id = int(request.form['product_type_id'])
    quantity = int(request.form['quantity'])
    
    if quantity <= 0:
        flash('Количество должно быть больше нуля!', 'danger')
        return redirect(url_for('index'))
        
    import datetime
    today = datetime.date.today().strftime('%Y-%m-%d')
    
    conn = get_db_connection()
    conn.execute('''
        INSERT INTO client_order (user_id, product_type_id, quantity, order_date, status)
        VALUES (?, ?, ?, ?, ?)
    ''', (session['user_id'], product_type_id, quantity, today, 'В обработке'))
    conn.commit()
    conn.close()
    
    flash('Заказ успешно оформлен и передан в обработку цеха!', 'success')
    return redirect(url_for('index'))

# ================= 2. АВТОМАТИЗАЦИЯ: СБОРКА ИЗДЕЛИЯ =================
@app.route('/assemble', methods=['GET', 'POST'])
@login_required('worker')
def assemble():
    conn = get_db_connection()
    if request.method == 'POST':
        product_id = int(request.form['product_type_id'])
        target_cell = request.form['cell_number'].strip()
        qty_to_build = int(request.form['quantity'])

        if not target_cell:
            flash('Ошибка: Номер ячейки не может быть пустым!', 'danger')
            conn.close()
            return redirect(url_for('assemble'))

        # 1. Узнаем спецификацию: какие детали нужны
        composition = conn.execute('SELECT part_id, quantity FROM product_composition WHERE product_type_id = ?', (product_id,)).fetchall()
        
        if not composition:
            flash('Ошибка: Для этого изделия не задан состав (спецификация)!', 'danger')
            conn.close()
            return redirect(url_for('assemble'))

        # 2. Проверяем остатки на складе деталей
        can_build = True
        for comp in composition:
            needed = comp['quantity'] * qty_to_build
            stock_row = conn.execute('SELECT SUM(quantity_stored) as total FROM part_warehouse_cell WHERE part_id = ?', (comp['part_id'],)).fetchone()
            stock = stock_row['total'] if stock_row['total'] else 0
            
            if stock < needed:
                can_build = False
                part_name = conn.execute('SELECT name FROM part WHERE id_part = ?', (comp['part_id'],)).fetchone()['name']
                flash(f'Ошибка сборки! Не хватает детали "{part_name}". Нужно: {needed}, есть на складе: {stock}', 'danger')
                break

        # 3. Если всего хватает -> списываем детали и добавляем изделие
        if can_build:
            try:
                for comp in composition:
                    needed = comp['quantity'] * qty_to_build
                    # Берем ячейки, где лежит нужная деталь
                    cells = conn.execute('SELECT id_part_cell, quantity_stored FROM part_warehouse_cell WHERE part_id = ? AND quantity_stored > 0 ORDER BY id_part_cell ASC', (comp['part_id'],)).fetchall()
                    
                    for cell in cells:
                        if needed <= 0: 
                            break
                        take = min(needed, cell['quantity_stored'])
                        # Списываем со склада деталей
                        conn.execute('UPDATE part_warehouse_cell SET quantity_stored = quantity_stored - ? WHERE id_part_cell = ?', (take, cell['id_part_cell']))
                        needed -= take

                # Кладем готовое изделие на склад готовой продукции
                existing_cell = conn.execute('SELECT id_product_cell FROM product_warehouse_cell WHERE cell_number = ? AND product_type_id = ?', (target_cell, product_id)).fetchone()
                if existing_cell:
                    conn.execute('UPDATE product_warehouse_cell SET quantity_stored = quantity_stored + ? WHERE id_product_cell = ?', (qty_to_build, existing_cell['id_product_cell']))
                else:
                    conn.execute('INSERT INTO product_warehouse_cell (cell_number, product_type_id, quantity_stored) VALUES (?, ?, ?)', (target_cell, product_id, qty_to_build))
                
                conn.commit()
                flash('Успех! Сборка завершена. Детали списаны, готовое изделие добавлено в ячейку.', 'success')
            except Exception as e:
                conn.rollback()
                flash(f'Системная ошибка при сборке: {str(e)}', 'danger')

        conn.close()
        return redirect(url_for('assemble'))

    products = conn.execute('SELECT * FROM product_type').fetchall()
    conn.close()
    return render_template('assemble.html', products=products)

# ================= 3. ЭКСПОРТ CSV (Остатки на складах) =================
@app.route('/export')
@login_required('worker')
def export():
    conn = get_db_connection()
    data = conn.execute('''
        SELECT p.name, p.article, SUM(c.quantity_stored) as total 
        FROM part p 
        LEFT JOIN part_warehouse_cell c ON p.id_part = c.part_id 
        GROUP BY p.id_part
    ''').fetchall()
    conn.close()

    si = io.StringIO()
    si.write('\ufeff')
    cw = csv.writer(si)
    cw.writerow(['Название детали', 'Артикул', 'Остаток на складе (шт)'])
    for row in data:
        cw.writerow([row['name'], row['article'], row['total'] if row['total'] else 0])
        
    output = Response(si.getvalue())
    output.headers["Content-Disposition"] = "attachment; filename=parts_stock_report.csv"
    output.headers["Content-type"] = "text/csv; charset=utf-8-sig"
    return output

# ================= 4. ВЕДОМОСТЬ ДЕФИЦИТА ПОД МЕСЯЧНЫЙ ПЛАН =================
@app.route('/reports', methods=['GET', 'POST'])
@login_required('worker')
def reports():
    conn = get_db_connection()
    products = conn.execute('SELECT * FROM product_type').fetchall()
    plans = get_production_plans(conn)
    
    if request.method == 'POST':
        new_plans = {}
        for p in products:
            key = f"plan_{p['id_product_type']}"
            val = request.form.get(key, 0)
            try:
                new_plans[str(p['id_product_type'])] = int(val)
            except ValueError:
                new_plans[str(p['id_product_type'])] = 0
        session['production_plans'] = new_plans
        session.modified = True
        flash('Месячный план производства успешно обновлен!', 'success')
        return redirect(url_for('reports'))

    parts = conn.execute('SELECT * FROM part').fetchall()
    missing_data = []
    
    for part in parts:
        part_id = part['id_part']
        total_needed = 0
        for p in products:
            p_id_str = str(p['id_product_type'])
            plan_qty = plans.get(p_id_str, 0)
            if plan_qty > 0:
                comp = conn.execute('SELECT quantity FROM product_composition WHERE product_type_id = ? AND part_id = ?', (p['id_product_type'], part_id)).fetchone()
                if comp:
                    total_needed += plan_qty * comp['quantity']
        
        stock_row = conn.execute('SELECT SUM(quantity_stored) as total FROM part_warehouse_cell WHERE part_id = ?', (part_id,)).fetchone()
        in_stock = stock_row['total'] if stock_row['total'] else 0
        
        if total_needed > in_stock:
            missing_data.append({
                'part_name': part['name'],
                'article': part['article'],
                'total_needed': total_needed,
                'in_stock': in_stock,
                'to_order': total_needed - in_stock
            })
            
    conn.close()
    
    plans_list = []
    for p in products:
        p_id_str = str(p['id_product_type'])
        plans_list.append({
            'id_product_type': p['id_product_type'],
            'name': p['name'],
            'target_quantity': plans.get(p_id_str, 0)
        })
        
    return render_template('reports.html', plans=plans_list, missing_data=missing_data)

@app.route('/export_csv')
@login_required('worker')
def export_csv():
    conn = get_db_connection()
    products = conn.execute('SELECT * FROM product_type').fetchall()
    plans = get_production_plans(conn)
    
    parts = conn.execute('SELECT * FROM part').fetchall()
    missing_data = []
    
    for part in parts:
        part_id = part['id_part']
        total_needed = 0
        for p in products:
            p_id_str = str(p['id_product_type'])
            plan_qty = plans.get(p_id_str, 0)
            if plan_qty > 0:
                comp = conn.execute('SELECT quantity FROM product_composition WHERE product_type_id = ? AND part_id = ?', (p['id_product_type'], part_id)).fetchone()
                if comp:
                    total_needed += plan_qty * comp['quantity']
        
        stock_row = conn.execute('SELECT SUM(quantity_stored) as total FROM part_warehouse_cell WHERE part_id = ?', (part_id,)).fetchone()
        in_stock = stock_row['total'] if stock_row['total'] else 0
        
        if total_needed > in_stock:
            missing_data.append({
                'part_name': part['name'],
                'article': part['article'],
                'total_needed': total_needed,
                'in_stock': in_stock,
                'to_order': total_needed - in_stock
            })
            
    conn.close()
    
    si = io.StringIO()
    si.write('\ufeff')
    cw = csv.writer(si)
    cw.writerow(['Деталь', 'Артикул', 'Необходимо по программе', 'Есть на складе', 'Требуется заказать (Дефицит)'])
    for row in missing_data:
        cw.writerow([row['part_name'], row['article'], row['total_needed'], row['in_stock'], row['to_order']])
        
    output = Response(si.getvalue())
    output.headers["Content-Disposition"] = "attachment; filename=parts_purchase_order.csv"
    output.headers["Content-type"] = "text/csv; charset=utf-8-sig"
    return output

# ================= 5. СПРАВОЧНИК: ДЕТАЛИ =================
@app.route('/parts', methods=['GET', 'POST'])
@login_required('worker')
def parts():
    conn = get_db_connection()
    if request.method == 'POST':
        name = request.form['name'].strip()
        article = request.form['article'].strip()
        if not name or not article:
            flash('Заполните все поля!', 'danger')
        else:
            try:
                conn.execute('INSERT INTO part (name, article) VALUES (?, ?)', (name, article))
                conn.commit()
                flash('Новая деталь успешно добавлена!', 'success')
            except sqlite3.IntegrityError:
                flash('Ошибка: Деталь с таким артикулом уже существует!', 'danger')
        return redirect(url_for('parts'))
    
    data = conn.execute('SELECT * FROM part').fetchall()
    conn.close()
    return render_template('parts.html', data=data)

@app.route('/delete_part/<int:id>')
@login_required('worker')
def delete_part(id):
    conn = get_db_connection()
    try:
        conn.execute('DELETE FROM part WHERE id_part = ?', (id,))
        conn.commit()
        flash('Деталь удалена из номенклатуры.', 'warning')
    except Exception as e:
        flash('Ошибка удаления! Возможно, деталь задействована в спецификациях или хранится на складе.', 'danger')
    conn.close()
    return redirect(url_for('parts'))

# ================= 6. СПРАВОЧНИК: ТИПЫ ИЗДЕЛИЙ =================
@app.route('/products', methods=['GET', 'POST'])
@login_required('worker')
def products():
    conn = get_db_connection()
    if request.method == 'POST':
        name = request.form['name'].strip()
        description = request.form['description'].strip()
        if not name:
            flash('Название изделия не может быть пустым!', 'danger')
        else:
            try:
                conn.execute('INSERT INTO product_type (name, description) VALUES (?, ?)', (name, description))
                conn.commit()
                flash('Новый тип изделия успешно добавлен!', 'success')
            except sqlite3.IntegrityError:
                flash('Ошибка: Изделие с таким названием уже существует!', 'danger')
        return redirect(url_for('products'))
    
    data = conn.execute('SELECT * FROM product_type').fetchall()
    conn.close()
    return render_template('products.html', data=data)

@app.route('/delete_product/<int:id>')
@login_required('worker')
def delete_product(id):
    conn = get_db_connection()
    try:
        conn.execute('DELETE FROM product_type WHERE id_product_type = ?', (id,))
        conn.commit()
        flash('Тип изделия удален.', 'warning')
    except Exception as e:
        flash('Ошибка удаления! Возможно, тип изделия используется в спецификациях или хранится на складе.', 'danger')
    conn.close()
    return redirect(url_for('products'))

# ================= 7. СПРАВОЧНИК: СПЕЦИФИКАЦИИ (СОСТАВ) =================
@app.route('/compositions', methods=['GET', 'POST'])
@login_required('worker')
def compositions():
    conn = get_db_connection()
    if request.method == 'POST':
        product_type_id = int(request.form['product_type_id'])
        part_id = int(request.form['part_id'])
        quantity = int(request.form['quantity'])
        
        if quantity <= 0:
            flash('Количество деталей должно быть больше нуля!', 'danger')
        else:
            try:
                conn.execute('INSERT INTO product_composition (product_type_id, part_id, quantity) VALUES (?, ?, ?)', 
                             (product_type_id, part_id, quantity))
                conn.commit()
                flash('Связь (состав изделия) успешно добавлена!', 'success')
            except sqlite3.IntegrityError:
                conn.execute('UPDATE product_composition SET quantity = ? WHERE product_type_id = ? AND part_id = ?', 
                             (quantity, product_type_id, part_id))
                conn.commit()
                flash('Количество деталей в спецификации успешно обновлено!', 'success')
        return redirect(url_for('compositions'))
    
    data = conn.execute('''
        SELECT c.product_type_id, c.part_id, c.quantity, p.name as product_name, d.name as part_name 
        FROM product_composition c
        JOIN product_type p ON c.product_type_id = p.id_product_type
        JOIN part d ON c.part_id = d.id_part
    ''').fetchall()
    
    products_list = conn.execute('SELECT * FROM product_type').fetchall()
    parts_list = conn.execute('SELECT * FROM part').fetchall()
    conn.close()
    return render_template('compositions.html', data=data, products=products_list, parts=parts_list)

@app.route('/delete_composition/<int:prod_id>/<int:part_id>')
@login_required('worker')
def delete_composition(prod_id, part_id):
    conn = get_db_connection()
    conn.execute('DELETE FROM product_composition WHERE product_type_id = ? AND part_id = ?', (prod_id, part_id))
    conn.commit()
    conn.close()
    flash('Компонент удален из спецификации.', 'warning')
    return redirect(url_for('compositions'))

# ================= 8. УЧЕТ СКЛАДА ДЕТАЛЕЙ =================
@app.route('/part_cells', methods=['GET', 'POST'])
@login_required('worker')
def part_cells():
    conn = get_db_connection()
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'receive':
            cell_number = request.form['cell_number'].strip().upper()
            part_id = int(request.form['part_id'])
            qty = int(request.form['quantity'])
            
            if not cell_number or qty <= 0:
                flash('Неверно заполнены данные приема!', 'danger')
            else:
                other_part = conn.execute('SELECT part_id FROM part_warehouse_cell WHERE cell_number = ? AND part_id != ?', (cell_number, part_id)).fetchone()
                if other_part:
                    other_name = conn.execute('SELECT name FROM part WHERE id_part = ?', (other_part['part_id'],)).fetchone()['name']
                    flash(f'Ошибка: Ячейка {cell_number} уже занята деталью "{other_name}"!', 'danger')
                else:
                    existing = conn.execute('SELECT id_part_cell, quantity_stored FROM part_warehouse_cell WHERE cell_number = ? AND part_id = ?', (cell_number, part_id)).fetchone()
                    if existing:
                        conn.execute('UPDATE part_warehouse_cell SET quantity_stored = quantity_stored + ? WHERE id_part_cell = ?', (qty, existing['id_part_cell']))
                    else:
                        conn.execute('INSERT INTO part_warehouse_cell (cell_number, part_id, quantity_stored) VALUES (?, ?, ?)', (cell_number, part_id, qty))
                    conn.commit()
                    flash(f'Детали успешно приняты в ячейку {cell_number}!', 'success')
                    
        elif action == 'issue':
            cell_id = int(request.form['cell_id'])
            qty = int(request.form['quantity'])
            
            cell = conn.execute('SELECT * FROM part_warehouse_cell WHERE id_part_cell = ?', (cell_id,)).fetchone()
            if not cell or qty <= 0:
                flash('Неверные параметры отпуска!', 'danger')
            elif cell['quantity_stored'] < qty:
                flash(f'Ошибка: В ячейке {cell["cell_number"]} всего {cell["quantity_stored"]} шт, а запрошено {qty} шт!', 'danger')
            else:
                conn.execute('UPDATE part_warehouse_cell SET quantity_stored = quantity_stored - ? WHERE id_part_cell = ?', (qty, cell_id))
                conn.commit()
                flash(f'Детали успешно отпущены из ячейки {cell["cell_number"]}!', 'success')
                
        return redirect(url_for('part_cells'))
        
    data = conn.execute('''
        SELECT c.*, p.name as part_name, p.article as part_article 
        FROM part_warehouse_cell c
        JOIN part p ON c.part_id = p.id_part
        ORDER BY c.cell_number ASC
    ''').fetchall()
    
    parts_list = conn.execute('SELECT * FROM part').fetchall()
    conn.close()
    return render_template('part_cells.html', data=data, parts=parts_list)

@app.route('/delete_part_cell/<int:id>')
@login_required('worker')
def delete_part_cell(id):
    conn = get_db_connection()
    conn.execute('DELETE FROM part_warehouse_cell WHERE id_part_cell = ?', (id,))
    conn.commit()
    conn.close()
    flash('Ячейка склада деталей удалена из системы.', 'warning')
    return redirect(url_for('part_cells'))

# ================= 9. УЧЕТ СКЛАДА ГОТОВЫХ ИЗДЕЛИЙ =================
@app.route('/product_cells', methods=['GET', 'POST'])
@login_required('worker')
def product_cells():
    conn = get_db_connection()
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'receive':
            cell_number = request.form['cell_number'].strip().upper()
            product_type_id = int(request.form['product_type_id'])
            qty = int(request.form['quantity'])
            
            if not cell_number or qty <= 0:
                flash('Неверно заполнены данные приема!', 'danger')
            else:
                other_prod = conn.execute('SELECT product_type_id FROM product_warehouse_cell WHERE cell_number = ? AND product_type_id != ?', (cell_number, product_type_id)).fetchone()
                if other_prod:
                    other_name = conn.execute('SELECT name FROM product_type WHERE id_product_type = ?', (other_prod['product_type_id'],)).fetchone()['name']
                    flash(f'Ошибка: Ячейка {cell_number} уже занята готовым изделием "{other_name}"!', 'danger')
                else:
                    existing = conn.execute('SELECT id_product_cell, quantity_stored FROM product_warehouse_cell WHERE cell_number = ? AND product_type_id = ?', (cell_number, product_type_id)).fetchone()
                    if existing:
                        conn.execute('UPDATE product_warehouse_cell SET quantity_stored = quantity_stored + ? WHERE id_product_cell = ?', (qty, existing['id_product_cell']))
                    else:
                        conn.execute('INSERT INTO product_warehouse_cell (cell_number, product_type_id, quantity_stored) VALUES (?, ?, ?)', (cell_number, product_type_id, qty))
                    conn.commit()
                    flash(f'Готовые изделия успешно приняты в ячейку {cell_number}!', 'success')
                    
        elif action == 'issue':
            cell_id = int(request.form['cell_id'])
            qty = int(request.form['quantity'])
            
            cell = conn.execute('SELECT * FROM product_warehouse_cell WHERE id_product_cell = ?', (cell_id,)).fetchone()
            if not cell or qty <= 0:
                flash('Неверные параметры отпуска!', 'danger')
            elif cell['quantity_stored'] < qty:
                flash(f'Ошибка: В ячейке {cell["cell_number"]} всего {cell["quantity_stored"]} шт, а запрошено {qty} шт!', 'danger')
            else:
                conn.execute('UPDATE product_warehouse_cell SET quantity_stored = quantity_stored - ? WHERE id_product_cell = ?', (qty, cell_id))
                conn.commit()
                flash(f'Готовые изделия успешно отпущены из ячейки {cell["cell_number"]}!', 'success')
                
        return redirect(url_for('product_cells'))
        
    data = conn.execute('''
        SELECT c.*, p.name as product_name 
        FROM product_warehouse_cell c
        JOIN product_type p ON c.product_type_id = p.id_product_type
        ORDER BY c.cell_number ASC
    ''').fetchall()
    
    products_list = conn.execute('SELECT * FROM product_type').fetchall()
    conn.close()
    return render_template('product_cells.html', data=data, products=products_list)

@app.route('/delete_product_cell/<int:id>')
@login_required('worker')
def delete_product_cell(id):
    conn = get_db_connection()
    conn.execute('DELETE FROM product_warehouse_cell WHERE id_product_cell = ?', (id,))
    conn.commit()
    conn.close()
    flash('Ячейка склада готовых изделий удалена из системы.', 'warning')
    return redirect(url_for('product_cells'))

# ================= 10. ПОЛУЧЕНИЕ СПРАВОК (ОТЧЕТЫ И ИНФОРМАЦИЯ) =================
@app.route('/info', methods=['GET', 'POST'])
@login_required('worker')
def info():
    conn = get_db_connection()
    products = conn.execute('SELECT * FROM product_type').fetchall()
    
    parts_stock = conn.execute('''
        SELECT p.name, p.article, pwc.cell_number, pwc.quantity_stored
        FROM part_warehouse_cell pwc
        JOIN part p ON pwc.part_id = p.id_part
        WHERE pwc.quantity_stored > 0
        ORDER BY p.name ASC, pwc.cell_number ASC
    ''').fetchall()
    
    products_stock = conn.execute('''
        SELECT pt.name, pwc.cell_number, pwc.quantity_stored
        FROM product_warehouse_cell pwc
        JOIN product_type pt ON pwc.product_type_id = pt.id_product_type
        WHERE pwc.quantity_stored > 0
        ORDER BY pt.name ASC, pwc.cell_number ASC
    ''').fetchall()

    calc_results = None
    selected_product = None
    qty_calc = 0
    
    if request.method == 'POST' and 'calc_product_id' in request.form:
        prod_id = int(request.form['calc_product_id'])
        qty_calc = int(request.form.get('calc_quantity', 1))
        
        selected_product = conn.execute('SELECT * FROM product_type WHERE id_product_type = ?', (prod_id,)).fetchone()
        
        composition = conn.execute('''
            SELECT pc.part_id, p.name, p.article, pc.quantity
            FROM product_composition pc
            JOIN part p ON pc.part_id = p.id_part
            WHERE pc.product_type_id = ?
        ''', (prod_id,)).fetchall()
        
        calc_results = []
        for item in composition:
            part_id = item['part_id']
            needed = item['quantity'] * qty_calc
            stock_row = conn.execute('SELECT SUM(quantity_stored) as total FROM part_warehouse_cell WHERE part_id = ?', (part_id,)).fetchone()
            in_stock = stock_row['total'] if stock_row['total'] else 0
            
            calc_results.append({
                'part_name': item['name'],
                'article': item['article'],
                'needed': needed,
                'in_stock': in_stock,
                'deficit': max(0, needed - in_stock)
            })

    conn.close()
    return render_template('info.html', 
                           products=products, 
                           parts_stock=parts_stock, 
                           products_stock=products_stock, 
                           calc_results=calc_results, 
                           selected_product=selected_product, 
                           qty_calc=qty_calc)

# ================= 11. ЗАКАЗЫ КЛИЕНТОВ (ДЛЯ РАБОТНИКА) =================
@app.route('/orders')
@login_required('worker')
def orders():
    conn = get_db_connection()
    orders_list = conn.execute('''
        SELECT co.*, pt.name as product_name, u.username as client_name
        FROM client_order co
        JOIN product_type pt ON co.product_type_id = pt.id_product_type
        JOIN user u ON co.user_id = u.id_user
        ORDER BY co.id_order DESC
    ''').fetchall()
    conn.close()
    return render_template('orders.html', orders=orders_list)

@app.route('/complete_order/<int:id>')
@login_required('worker')
def complete_order(id):
    conn = get_db_connection()
    order = conn.execute('SELECT * FROM client_order WHERE id_order = ?', (id,)).fetchone()
    
    if not order:
        flash('Заказ не найден!', 'danger')
        conn.close()
        return redirect(url_for('orders'))
        
    if order['status'] == 'Собран':
        flash('Заказ уже собран и отгружен!', 'info')
        conn.close()
        return redirect(url_for('orders'))
        
    product_type_id = order['product_type_id']
    qty = order['quantity']
    
    # Проверяем, сколько готовых изделий есть на складе
    stock_row = conn.execute('SELECT SUM(quantity_stored) as total FROM product_warehouse_cell WHERE product_type_id = ?', (product_type_id,)).fetchone()
    in_stock = stock_row['total'] if stock_row['total'] else 0
    
    if in_stock >= qty:
        # Списываем со склада готовых изделий
        cells = conn.execute('SELECT id_product_cell, quantity_stored FROM product_warehouse_cell WHERE product_type_id = ? AND quantity_stored > 0 ORDER BY id_product_cell ASC', (product_type_id,)).fetchall()
        needed = qty
        
        try:
            for cell in cells:
                if needed <= 0:
                    break
                take = min(needed, cell['quantity_stored'])
                conn.execute('UPDATE product_warehouse_cell SET quantity_stored = quantity_stored - ? WHERE id_product_cell = ?', (take, cell['id_product_cell']))
                needed -= take
                
            conn.execute('UPDATE client_order SET status = "Собран" WHERE id_order = ?', (id,))
            conn.commit()
            flash('Заказ успешно собран и отгружен со склада готовой продукции!', 'success')
        except Exception as e:
            conn.rollback()
            flash(f'Ошибка при отгрузке заказа: {str(e)}', 'danger')
    else:
        product_name = conn.execute('SELECT name FROM product_type WHERE id_product_type = ?', (product_type_id,)).fetchone()['name']
        flash(f'Ошибка: Недостаточно готовых изделий "{product_name}" на складе готовой продукции! Требуется: {qty}, в наличии: {in_stock}. Перейдите на вкладку Сборка.', 'danger')
        
    conn.close()
    return redirect(url_for('orders'))

@app.route('/delete_order/<int:id>')
@login_required('worker')
def delete_order(id):
    conn = get_db_connection()
    conn.execute('DELETE FROM client_order WHERE id_order = ?', (id,))
    conn.commit()
    conn.close()
    flash('Заказ удален.', 'warning')
    return redirect(url_for('orders'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
