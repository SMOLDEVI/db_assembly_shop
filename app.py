from flask import Flask, render_template, request, redirect, url_for, flash, Response, session
import sqlite3
import csv
import io

app = Flask(__name__)
app.secret_key = 'super_secret_key' # Обязательно для работы flash-уведомлений и сохранения плана в сессии

def get_db_connection():
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    return conn

# Помощник для получения плана из сессии или создания дефолтного плана
def get_production_plans(conn):
    if 'production_plans' not in session or not session['production_plans']:
        products = conn.execute('SELECT id_product_type FROM product_type').fetchall()
        default_plans = {}
        # Задаем дефолтные объемы выпуска для расчета дефицита
        defaults = {1: 10, 2: 5, 3: 2, 4: 1, 5: 3}
        for p in products:
            p_id = p['id_product_type']
            default_plans[str(p_id)] = defaults.get(p_id, 2)
        session['production_plans'] = default_plans
        session.modified = True
    return session['production_plans']

# ================= 1. ГЛАВНАЯ (ДАШБОРД С ДИАГРАММОЙ) =================
@app.route('/')
def index():
    conn = get_db_connection()
    # Собираем данные для графика: название изделия и общее количество на всех ячейках
    items = conn.execute('''
        SELECT pt.name, SUM(pwc.quantity_stored) as total 
        FROM product_warehouse_cell pwc
        JOIN product_type pt ON pwc.product_type_id = pt.id_product_type
        GROUP BY pt.id_product_type HAVING total > 0
    ''').fetchall()
    
    # Расчет дефицита деталей для статистики дашборда на основе плана
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

# ================= 2. АВТОМАТИЗАЦИЯ: СБОРКА ИЗДЕЛИЯ =================
@app.route('/assemble', methods=['GET', 'POST'])
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
def export():
    conn = get_db_connection()
    # Выгружаем список деталей и их остатки
    data = conn.execute('''
        SELECT p.name, p.article, SUM(c.quantity_stored) as total 
        FROM part p 
        LEFT JOIN part_warehouse_cell c ON p.id_part = c.part_id 
        GROUP BY p.id_part
    ''').fetchall()
    conn.close()

    si = io.StringIO()
    si.write('\ufeff') # Добавляем BOM для Excel
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

    # Вычисляем дефицит
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
    si.write('\ufeff') # BOM для Excel
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
                # Если уже есть, обновим количество
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
def delete_composition(prod_id, part_id):
    conn = get_db_connection()
    conn.execute('DELETE FROM product_composition WHERE product_type_id = ? AND part_id = ?', (prod_id, part_id))
    conn.commit()
    conn.close()
    flash('Компонент удален из спецификации.', 'warning')
    return redirect(url_for('compositions'))

# ================= 8. УЧЕТ СКЛАДА ДЕТАЛЕЙ =================
@app.route('/part_cells', methods=['GET', 'POST'])
def part_cells():
    conn = get_db_connection()
    
    # Обработка POST-запросов (прием деталей / отпуск деталей)
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'receive':  # Прием деталей
            cell_number = request.form['cell_number'].strip().upper()
            part_id = int(request.form['part_id'])
            qty = int(request.form['quantity'])
            
            if not cell_number or qty <= 0:
                flash('Неверно заполнены данные приема!', 'danger')
            else:
                # Проверим, не специализируется ли ячейка на другой детали
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
                    
        elif action == 'issue':  # Отпуск деталей со склада
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
def delete_part_cell(id):
    conn = get_db_connection()
    conn.execute('DELETE FROM part_warehouse_cell WHERE id_part_cell = ?', (id,))
    conn.commit()
    conn.close()
    flash('Ячейка склада деталей удалена из системы.', 'warning')
    return redirect(url_for('part_cells'))

# ================= 9. УЧЕТ СКЛАДА ГОТОВЫХ ИЗДЕЛИЙ =================
@app.route('/product_cells', methods=['GET', 'POST'])
def product_cells():
    conn = get_db_connection()
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'receive':  # Прием изделий
            cell_number = request.form['cell_number'].strip().upper()
            product_type_id = int(request.form['product_type_id'])
            qty = int(request.form['quantity'])
            
            if not cell_number or qty <= 0:
                flash('Неверно заполнены данные приема!', 'danger')
            else:
                # Проверим, не специализируется ли ячейка на другом типе изделия
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
                    
        elif action == 'issue':  # Отпуск готовых изделий
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
def delete_product_cell(id):
    conn = get_db_connection()
    conn.execute('DELETE FROM product_warehouse_cell WHERE id_product_cell = ?', (id,))
    conn.commit()
    conn.close()
    flash('Ячейка склада готовых изделий удалена из системы.', 'warning')
    return redirect(url_for('product_cells'))

# ================= 10. ПОЛУЧЕНИЕ СПРАВОК (ОТЧЕТЫ И ИНФОРМАЦИЯ) =================
@app.route('/info', methods=['GET', 'POST'])
def info():
    conn = get_db_connection()
    products = conn.execute('SELECT * FROM product_type').fetchall()
    
    # 1. Справка о наличии деталей на складе (сводный остаток)
    parts_stock = conn.execute('''
        SELECT p.name, p.article, pwc.cell_number, pwc.quantity_stored
        FROM part_warehouse_cell pwc
        JOIN part p ON pwc.part_id = p.id_part
        WHERE pwc.quantity_stored > 0
        ORDER BY p.name ASC, pwc.cell_number ASC
    ''').fetchall()
    
    # 2. Справка о наличии готовых изделий (сводный остаток)
    products_stock = conn.execute('''
        SELECT pt.name, pwc.cell_number, pwc.quantity_stored
        FROM product_warehouse_cell pwc
        JOIN product_type pt ON pwc.product_type_id = pt.id_product_type
        WHERE pwc.quantity_stored > 0
        ORDER BY pt.name ASC, pwc.cell_number ASC
    ''').fetchall()

    # 3. Расчет комплектующих и дефицита для сборки нескольких изделий
    calc_results = None
    selected_product = None
    qty_calc = 0
    
    if request.method == 'POST' and 'calc_product_id' in request.form:
        prod_id = int(request.form['calc_product_id'])
        qty_calc = int(request.form.get('calc_quantity', 1))
        
        selected_product = conn.execute('SELECT * FROM product_type WHERE id_product_type = ?', (prod_id,)).fetchone()
        
        # Получаем спецификацию состава
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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
