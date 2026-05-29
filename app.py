from flask import Flask, render_template, request, redirect, url_for, flash, Response
import sqlite3
import csv
import io

app = Flask(__name__)
app.secret_key = 'super_secret_key' # Обязательно для работы flash-уведомлений

def get_db_connection():
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    return conn

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
    
    # Расчет дефицита для статистики дашборда
    parts = conn.execute('SELECT id_part FROM part').fetchall()
    deficit_count = 0
    for part in parts:
        part_id = part['id_part']
        needed_row = conn.execute('''
            SELECT SUM(COALESCE(pp.target_quantity, 0) * pc.quantity) as total_needed
            FROM product_composition pc
            JOIN production_plan pp ON pc.product_type_id = pp.product_type_id
            WHERE pc.part_id = ?
        ''', (part_id,)).fetchone()
        total_needed = needed_row['total_needed'] if needed_row['total_needed'] else 0
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
        target_cell = request.form['cell_number']
        qty_to_build = int(request.form['quantity'])

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
                # Получаем имя детали для красивого вывода ошибки
                part_name = conn.execute('SELECT name FROM part WHERE id_part = ?', (comp['part_id'],)).fetchone()['name']
                flash(f'Ошибка сборки! Не хватает детали "{part_name}". Нужно: {needed}, есть на складе: {stock}', 'danger')
                break

        # 3. Если всего хватает -> списываем детали и добавляем изделие
        if can_build:
            try:
                for comp in composition:
                    needed = comp['quantity'] * qty_to_build
                    # Берем ячейки, где лежит нужная деталь
                    cells = conn.execute('SELECT id_part_cell, quantity_stored FROM part_warehouse_cell WHERE part_id = ? AND quantity_stored > 0', (comp['part_id'],)).fetchall()
                    
                    for cell in cells:
                        if needed <= 0: 
                            break
                        take = min(needed, cell['quantity_stored'])
                        # Списываем со склада деталей
                        conn.execute('UPDATE part_warehouse_cell SET quantity_stored = quantity_stored - ? WHERE id_part_cell = ?', (take, cell['id_part_cell']))
                        needed -= take

                # Кладем готовое изделие на склад изделий
                existing_cell = conn.execute('SELECT id_product_cell FROM product_warehouse_cell WHERE cell_number = ? AND product_type_id = ?', (target_cell, product_id)).fetchone()
                if existing_cell:
                    conn.execute('UPDATE product_warehouse_cell SET quantity_stored = quantity_stored + ? WHERE id_product_cell = ?', (qty_to_build, existing_cell['id_product_cell']))
                else:
                    conn.execute('INSERT INTO product_warehouse_cell (cell_number, product_type_id, quantity_stored) VALUES (?, ?, ?)', (target_cell, product_id, qty_to_build))
                
                conn.commit()
                flash('Успех! Сборка завершена. Детали списаны, готовое изделие добавлено в ячейку.', 'success')
            except Exception as e:
                conn.rollback()
                flash('Системная ошибка при записи в базу данных.', 'danger')

        conn.close()
        return redirect(url_for('assemble'))

    products = conn.execute('SELECT * FROM product_type').fetchall()
    conn.close()
    return render_template('assemble.html', products=products)

# ================= 3. ОТЧЕТ CSV (ЭКСПОРТ) =================
@app.route('/export')
def export():
    conn = get_db_connection()
    # Выгружаем список всех деталей и их текущие остатки
    data = conn.execute('''
        SELECT p.name, p.article, SUM(c.quantity_stored) as total 
        FROM part p 
        LEFT JOIN part_warehouse_cell c ON p.id_part = c.part_id 
        GROUP BY p.id_part
    ''').fetchall()
    conn.close()

    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(['Название детали', 'Артикул', 'Остаток на складе (шт)']) # Заголовки
    for row in data:
        cw.writerow([row['name'], row['article'], row['total'] if row['total'] else 0])
        
    output = Response(si.getvalue())
    output.headers["Content-Disposition"] = "attachment; filename=warehouse_inventory_report.csv"
    output.headers["Content-type"] = "text/csv; charset=utf-8-sig" # utf-8-sig нужен для корректного отображения кириллицы в Excel
    return output

# ================= 3.1. ОТЧЕТ О ДЕФИЦИТЕ ДЕТАЛЕЙ ДЛЯ ПЛАНА =================
@app.route('/reports', methods=['GET'])
def reports():
    conn = get_db_connection()
    
    # 1. Получаем текущие планы выпуска для отображения в форме
    plans = conn.execute('''
        SELECT pt.id_product_type, pt.name, COALESCE(pp.target_quantity, 0) as target_quantity
        FROM product_type pt
        LEFT JOIN production_plan pp ON pt.id_product_type = pp.product_type_id
    ''').fetchall()
    
    # 2. Вычисляем дефицит деталей
    parts = conn.execute('SELECT * FROM part').fetchall()
    missing_data = []
    
    for part in parts:
        part_id = part['id_part']
        # Сколько нужно по плану
        needed_row = conn.execute('''
            SELECT SUM(COALESCE(pp.target_quantity, 0) * pc.quantity) as total_needed
            FROM product_composition pc
            JOIN production_plan pp ON pc.product_type_id = pp.product_type_id
            WHERE pc.part_id = ?
        ''', (part_id,)).fetchone()
        total_needed = needed_row['total_needed'] if needed_row['total_needed'] else 0
        
        # Сколько на складе
        stock_row = conn.execute('SELECT SUM(quantity_stored) as total FROM part_warehouse_cell WHERE part_id = ?', (part_id,)).fetchone()
        in_stock = stock_row['total'] if stock_row['total'] else 0
        
        if total_needed > in_stock:
            missing_data.append({
                'part_name': part['name'],
                'total_needed': total_needed,
                'in_stock': in_stock,
                'to_order': total_needed - in_stock
            })
            
    conn.close()
    return render_template('reports.html', plans=plans, missing_data=missing_data)

@app.route('/update_plan', methods=['POST'])
def update_plan():
    conn = get_db_connection()
    try:
        # Получаем данные о планах из формы
        for key, val in request.form.items():
            if key.startswith('plan_'):
                product_id = int(key.split('_')[1])
                qty = int(val) if val else 0
                # Вставляем или обновляем план
                conn.execute('''
                    INSERT INTO production_plan (product_type_id, target_quantity)
                    VALUES (?, ?)
                    ON CONFLICT(product_type_id) DO UPDATE SET target_quantity = excluded.target_quantity
                ''', (product_id, qty))
        conn.commit()
        flash('План производства успешно обновлен!', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Ошибка при обновлении плана: {str(e)}', 'danger')
    conn.close()
    return redirect(url_for('reports'))

@app.route('/export_csv')
def export_csv():
    conn = get_db_connection()
    parts = conn.execute('SELECT * FROM part').fetchall()
    missing_data = []
    
    for part in parts:
        part_id = part['id_part']
        needed_row = conn.execute('''
            SELECT SUM(COALESCE(pp.target_quantity, 0) * pc.quantity) as total_needed
            FROM product_composition pc
            JOIN production_plan pp ON pc.product_type_id = pp.product_type_id
            WHERE pc.part_id = ?
        ''', (part_id,)).fetchone()
        total_needed = needed_row['total_needed'] if needed_row['total_needed'] else 0
        
        stock_row = conn.execute('SELECT SUM(quantity_stored) as total FROM part_warehouse_cell WHERE part_id = ?', (part_id,)).fetchone()
        in_stock = stock_row['total'] if stock_row['total'] else 0
        
        if total_needed > in_stock:
            missing_data.append({
                'part_name': part['name'],
                'total_needed': total_needed,
                'in_stock': in_stock,
                'to_order': total_needed - in_stock
            })
            
    conn.close()
    
    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(['Деталь', 'Нужно всего по плану', 'Есть на складе', 'Заказать у поставщика'])
    for row in missing_data:
        cw.writerow([row['part_name'], row['total_needed'], row['in_stock'], row['to_order']])
        
    output = Response(si.getvalue())
    output.headers["Content-Disposition"] = "attachment; filename=missing_parts_report.csv"
    output.headers["Content-type"] = "text/csv; charset=utf-8-sig"
    return output

# ================= 4. CRUD ТАБЛИЦЫ: part =================
@app.route('/parts', methods=['GET', 'POST'])
def parts():
    conn = get_db_connection()
    if request.method == 'POST':
        conn.execute('INSERT INTO part (name, article) VALUES (?, ?)', (request.form['name'], request.form['article']))
        conn.commit()
        flash('Новая деталь успешно добавлена!', 'success')
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
        flash('Деталь удалена.', 'warning')
    except:
        flash('Ошибка удаления! Возможно, деталь используется в других таблицах.', 'danger')
    conn.close()
    return redirect(url_for('parts'))

# ================= 5. CRUD ТАБЛИЦЫ: product_type =================
@app.route('/products', methods=['GET', 'POST'])
def products():
    conn = get_db_connection()
    if request.method == 'POST':
        conn.execute('INSERT INTO product_type (name, description) VALUES (?, ?)', (request.form['name'], request.form['description']))
        conn.commit()
        flash('Новое изделие успешно добавлено!', 'success')
        return redirect(url_for('products'))
    
    data = conn.execute('SELECT * FROM product_type').fetchall()
    conn.close()
    return render_template('products.html', data=data)

@app.route('/delete_product/<int:id>')
def delete_product(id):
    conn = get_db_connection()
    conn.execute('DELETE FROM product_type WHERE id_product_type = ?', (id,))
    conn.commit()
    conn.close()
    flash('Изделие удалено.', 'warning')
    return redirect(url_for('products'))

# ================= 6. CRUD ТАБЛИЦЫ: product_composition =================
@app.route('/compositions', methods=['GET', 'POST'])
def compositions():
    conn = get_db_connection()
    if request.method == 'POST':
        conn.execute('INSERT INTO product_composition (product_type_id, part_id, quantity) VALUES (?, ?, ?)', 
                     (request.form['product_type_id'], request.form['part_id'], request.form['quantity']))
        conn.commit()
        flash('Связь (спецификация) успешно добавлена!', 'success')
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
    flash('Спецификация удалена.', 'warning')
    return redirect(url_for('compositions'))

# ================= 7. CRUD ТАБЛИЦЫ: part_warehouse_cell =================
@app.route('/part_cells', methods=['GET', 'POST'])
def part_cells():
    conn = get_db_connection()
    if request.method == 'POST':
        conn.execute('INSERT INTO part_warehouse_cell (cell_number, part_id, quantity_stored) VALUES (?, ?, ?)', 
                     (request.form['cell_number'], request.form['part_id'], request.form['quantity_stored']))
        conn.commit()
        flash('Детали добавлены в ячейку склада!', 'success')
        return redirect(url_for('part_cells'))
    
    data = conn.execute('''
        SELECT c.*, p.name as part_name 
        FROM part_warehouse_cell c
        JOIN part p ON c.part_id = p.id_part
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
    flash('Ячейка склада деталей удалена.', 'warning')
    return redirect(url_for('part_cells'))

# ================= 8. CRUD ТАБЛИЦЫ: product_warehouse_cell =================
@app.route('/product_cells', methods=['GET', 'POST'])
def product_cells():
    conn = get_db_connection()
    if request.method == 'POST':
        conn.execute('INSERT INTO product_warehouse_cell (cell_number, product_type_id, quantity_stored) VALUES (?, ?, ?)', 
                     (request.form['cell_number'], request.form['product_type_id'], request.form['quantity_stored']))
        conn.commit()
        flash('Изделия добавлены в ячейку склада!', 'success')
        return redirect(url_for('product_cells'))
    
    data = conn.execute('''
        SELECT c.*, p.name as product_name 
        FROM product_warehouse_cell c
        JOIN product_type p ON c.product_type_id = p.id_product_type
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
    flash('Ячейка склада изделий удалена.', 'warning')
    return redirect(url_for('product_cells'))

if __name__ == '__main__':
    app.run(debug=True)
