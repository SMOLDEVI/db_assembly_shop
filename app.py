from flask import Flask, render_template, request, redirect, url_for, flash, Response, session, send_file
import sqlite3
import csv
import io
import doc_utils
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'super_secret_key' # Обязательно для работы flash-уведомлений и сохранения плана в сессии

@app.template_filter('pluralize_ru')
def pluralize_ru(count, one, few, many):
    if count % 10 == 1 and count % 100 != 11:
        return one
    elif 2 <= count % 10 <= 4 and (count % 100 < 10 or count % 100 >= 20):
        return few
    return many

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
    
    parts = conn.execute('SELECT * FROM part').fetchall()
    deficit_parts = []
    affected_product_ids = set()
    
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
            deficit_parts.append({
                'name': part['name'],
                'article': part['article'],
                'needed': total_needed,
                'stock': in_stock,
                'deficit': total_needed - in_stock,
            })
            # Найти все изделия, где эта деталь используется
            affected = conn.execute('''
                SELECT DISTINCT product_type_id FROM product_composition WHERE part_id = ?
            ''', (part_id,)).fetchall()
            for a in affected:
                affected_product_ids.add(a['product_type_id'])
    
    # Детализируем дефицит по изделиям
    deficit_products = []
    for pid in sorted(affected_product_ids):
        prod = conn.execute('SELECT * FROM product_type WHERE id_product_type = ?', (pid,)).fetchone()
        if prod:
            deficit_products.append(prod['name'])

    stats = {
        'total_products': conn.execute('SELECT SUM(quantity_stored) FROM product_warehouse_cell').fetchone()[0] or 0,
        'total_parts': conn.execute('SELECT SUM(quantity_stored) FROM part_warehouse_cell').fetchone()[0] or 0,
        'total_configs': conn.execute('SELECT COUNT(DISTINCT product_type_id) FROM product_composition').fetchone()[0] or 0,
        'deficit_count': deficit_count
    }
    conn.close()
    
    labels = [item['name'] for item in items]
    data = [item['total'] for item in items]
    return render_template('index.html', labels=labels, data=data, stats=stats, 
                           deficit_parts=deficit_parts, deficit_products=deficit_products)

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
    
    now = datetime.now()
    si = io.StringIO()
    si.write('\ufeff')
    cw = csv.writer(si, delimiter=';')
    
    cw.writerow(['ООО «ТехноПромСборка» — ЗАКАЗ НА ПОСТАВКУ ДЕТАЛЕЙ'])
    cw.writerow([f'Дата: {now.strftime("%d.%m.%Y")}'])
    cw.writerow([])
    cw.writerow(['Программа выпуска:'])
    for p in products:
        plan_qty = plans.get(str(p['id_product_type']), 0)
        if plan_qty > 0:
            cw.writerow([f'  • {p["name"]}: {plan_qty} шт.'])
    cw.writerow([])
    cw.writerow(['№;Деталь;Артикул;Требуется (шт.);В наличии (шт.);К ЗАКАЗУ (шт.)'])
    total_order = 0
    for i, row in enumerate(missing_data, 1):
        cw.writerow([i, row['part_name'], row['article'], row['total_needed'], row['in_stock'], row['to_order']])
        total_order += row['to_order']
    cw.writerow([])
    cw.writerow([f'ИТОГО К ЗАКАЗУ:;{total_order} шт.'])
    cw.writerow([])
    cw.writerow(['Заказчик:'])
    cw.writerow(['Генеральный директор'])
    cw.writerow(['__________________ /_______________/'])
    cw.writerow(['М.П.'])
    
    output = Response(si.getvalue())
    output.headers["Content-Disposition"] = f"attachment; filename=zakaz_na_postavku_{now.strftime('%Y%m%d')}.csv"
    output.headers["Content-type"] = "text/csv; charset=utf-8-sig"
    return output

@app.route('/export_order_docx')
def export_order_docx():
    conn = get_db_connection()
    products = conn.execute('SELECT * FROM product_type').fetchall()
    plans = get_production_plans(conn)
    all_parts = conn.execute('SELECT * FROM part').fetchall()
    
    missing_data = []
    for part in all_parts:
        part_id = part['id_part']
        total_needed = 0
        for p in products:
            plan_qty = plans.get(str(p['id_product_type']), 0)
            if plan_qty > 0:
                comp = conn.execute('SELECT quantity FROM product_composition WHERE product_type_id = ? AND part_id = ?',
                                   (p['id_product_type'], part_id)).fetchone()
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
                'to_order': total_needed - in_stock,
            })
    conn.close()
    
    production_plan = [(p['name'], plans.get(str(p['id_product_type']), 0)) for p in products]
    
    now = datetime.now()
    fname = f'zakaz_postavka_{now.strftime("%Y%m%d_%H%M")}.docx'
    out = doc_utils.build_purchase_order(missing_data, production_plan, fname)
    return send_file(out, as_attachment=True, download_name=f'Заказ_на_поставку_{now.strftime("%Y%m%d")}.docx')

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
        SELECT c.product_type_id, c.part_id, c.quantity, p.name as product_name, d.name as part_name, d.article as part_article
        FROM product_composition c
        JOIN product_type p ON c.product_type_id = p.id_product_type
        JOIN part d ON c.part_id = d.id_part
        ORDER BY p.name ASC, d.name ASC
    ''').fetchall()
    
    products_list = conn.execute('SELECT * FROM product_type ORDER BY id_product_type').fetchall()
    parts_list = conn.execute('SELECT * FROM part ORDER BY name').fetchall()
    
    # Группируем детали по изделиям для удобного отображения
    products_with_parts = []
    for p in products_list:
        parts = [dict(row) for row in data if row['product_type_id'] == p['id_product_type']]
        products_with_parts.append({
            'product': p,
            'parts': parts
        })
    
    conn.close()
    return render_template('compositions.html', products_with_parts=products_with_parts, products=products_list, parts=parts_list)

@app.route('/delete_composition/<int:prod_id>/<int:part_id>')
def delete_composition(prod_id, part_id):
    conn = get_db_connection()
    conn.execute('DELETE FROM product_composition WHERE product_type_id = ? AND part_id = ?', (prod_id, part_id))
    conn.commit()
    conn.close()
    flash('Компонент удален из спецификации.', 'warning')
    return redirect(url_for('compositions'))

# ================= 8. УЧЕТ СКЛАДА ДЕТАЛЕЙ =================
def gen_part_doc(template_name, cell_id, qty, doc_prefix):
    conn = get_db_connection()
    cell = conn.execute('''
        SELECT c.*, p.name as part_name, p.article as part_article
        FROM part_warehouse_cell c
        JOIN part p ON c.part_id = p.id_part
        WHERE c.id_part_cell = ?
    ''', (cell_id,)).fetchone()
    conn.close()
    if not cell:
        return None, None
    repl = doc_utils.default_replacements()
    repl['number'] = f'{doc_prefix}-{cell_id:04d}'
    repl['cell_number'] = cell['cell_number']
    repl['part_name'] = cell['part_name']
    repl['part_article'] = cell['part_article']
    repl['quantity'] = str(qty)
    repl['accepted_by'] = '___________'
    repl['issued_by'] = '___________'
    fname = f'{doc_prefix}_{cell_id}.docx'
    out = doc_utils.fill_template(template_name, repl, fname)
    return out, fname


@app.route('/doc/part/<int:cell_id>/<doc_name>')
def doc_serve_part(cell_id, doc_name):
    import os
    safe = os.path.basename(doc_name)
    path = os.path.join(doc_utils.OUTPUTS_DIR, safe)
    if not os.path.exists(path):
        flash('Документ не найден или был удалён.', 'danger')
        return redirect(url_for('part_cells'))
    return send_file(path, as_attachment=True, download_name=safe)

@app.route('/part_cells', methods=['GET', 'POST'])
def part_cells():
    conn = get_db_connection()
    
    if request.method == 'POST':
        action = request.form.get('action')
        with_doc = request.form.get('generate_doc') == '1'
        doc_link = None
        
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
                        cell_id = existing['id_part_cell']
                        conn.execute('UPDATE part_warehouse_cell SET quantity_stored = quantity_stored + ? WHERE id_part_cell = ?', (qty, cell_id))
                    else:
                        conn.execute('INSERT INTO part_warehouse_cell (cell_number, part_id, quantity_stored) VALUES (?, ?, ?)', (cell_number, part_id, qty))
                        conn.commit()
                        cell_id = conn.execute('SELECT id_part_cell FROM part_warehouse_cell WHERE cell_number = ? AND part_id = ?', (cell_number, part_id)).fetchone()['id_part_cell']
                    
                    conn.commit()
                    _, fname = gen_part_doc('part_receipt.docx', cell_id, qty, 'ПД')
                    doc_link = url_for('doc_serve_part', cell_id=cell_id, doc_name=fname) if with_doc and fname else None
                    msg = f'Детали успешно приняты в ячейку {cell_number}!'
                    if doc_link:
                        msg += f' <a href="{doc_link}" class="alert-link" download>Скачать акт приемки</a>'
                    flash(msg, 'success')
                    
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
                _, fname = gen_part_doc('part_issue.docx', cell_id, qty, 'ОТ')
                doc_link = url_for('doc_serve_part', cell_id=cell_id, doc_name=fname) if with_doc and fname else None
                msg = f'Детали успешно отпущены из ячейки {cell["cell_number"]}!'
                if doc_link:
                    msg += f' <a href="{doc_link}" class="alert-link" download>Скачать накладную отпуска</a>'
                flash(msg, 'success')
                
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
def gen_product_doc(template_name, cell_id, qty, doc_prefix):
    conn = get_db_connection()
    cell = conn.execute('''
        SELECT c.*, p.name as product_name, p.description as product_description
        FROM product_warehouse_cell c
        JOIN product_type p ON c.product_type_id = p.id_product_type
        WHERE c.id_product_cell = ?
    ''', (cell_id,)).fetchone()
    conn.close()
    if not cell:
        return None, None
    repl = doc_utils.default_replacements()
    repl['number'] = f'{doc_prefix}-{cell_id:04d}'
    repl['cell_number'] = cell['cell_number']
    repl['product_name'] = cell['product_name']
    repl['product_description'] = cell['product_description'] or ''
    repl['quantity'] = str(qty)
    repl['person'] = '___________'
    fname = f'{doc_prefix}_{cell_id}.docx'
    out = doc_utils.fill_template(template_name, repl, fname)
    return out, fname


@app.route('/doc/product/<int:cell_id>/<doc_name>')
def doc_serve_product(cell_id, doc_name):
    import os
    safe = os.path.basename(doc_name)
    path = os.path.join(doc_utils.OUTPUTS_DIR, safe)
    if not os.path.exists(path):
        flash('Документ не найден или был удалён.', 'danger')
        return redirect(url_for('product_cells'))
    return send_file(path, as_attachment=True, download_name=safe)

@app.route('/product_cells', methods=['GET', 'POST'])
def product_cells():
    conn = get_db_connection()
    
    if request.method == 'POST':
        action = request.form.get('action')
        with_doc = request.form.get('generate_doc') == '1'
        doc_link = None
        
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
                        cell_id = existing['id_product_cell']
                        conn.execute('UPDATE product_warehouse_cell SET quantity_stored = quantity_stored + ? WHERE id_product_cell = ?', (qty, cell_id))
                    else:
                        conn.execute('INSERT INTO product_warehouse_cell (cell_number, product_type_id, quantity_stored) VALUES (?, ?, ?)', (cell_number, product_type_id, qty))
                        conn.commit()
                        cell_id = conn.execute('SELECT id_product_cell FROM product_warehouse_cell WHERE cell_number = ? AND product_type_id = ?', (cell_number, product_type_id)).fetchone()['id_product_cell']
                    
                    conn.commit()
                    _, fname = gen_product_doc('product_receipt.docx', cell_id, qty, 'ПГИ')
                    doc_link = url_for('doc_serve_product', cell_id=cell_id, doc_name=fname) if with_doc and fname else None
                    msg = f'Готовые изделия успешно приняты в ячейку {cell_number}!'
                    if doc_link:
                        msg += f' <a href="{doc_link}" class="alert-link" download>Скачать акт приемки</a>'
                    flash(msg, 'success')
                    
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
                _, fname = gen_product_doc('product_issue.docx', cell_id, qty, 'ОТГИ')
                doc_link = url_for('doc_serve_product', cell_id=cell_id, doc_name=fname) if with_doc and fname else None
                msg = f'Готовые изделия успешно отпущены из ячейки {cell["cell_number"]}!'
                if doc_link:
                    msg += f' <a href="{doc_link}" class="alert-link" download>Скачать накладную отпуска</a>'
                flash(msg, 'success')
                
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

# ================= 11. КОМПЛЕКСНЫЙ ОТЧЁТ CSV (ВСЕ СПРАВКИ) =================
@app.route('/export_full_report')
def export_full_report():
    conn = get_db_connection()
    now = datetime.now()
    si = io.StringIO()
    si.write('\ufeff')
    cw = csv.writer(si, delimiter=';')

    # Шапка документа
    cw.writerow(['ООО «ТехноПромСборка» — Сборочный цех'])
    cw.writerow([f'Дата формирования: {now.strftime("%d.%m.%Y %H:%M")}'])
    cw.writerow([])
    cw.writerow(['=' * 80])
    cw.writerow([])

    # 1. Наличие деталей на складе
    cw.writerow(['1. СПРАВКА О НАЛИЧИИ ДЕТАЛЕЙ НА СКЛАДЕ'])
    cw.writerow(['Ячейка;Деталь;Артикул;Количество (шт.)'])
    parts = conn.execute('''
        SELECT pwc.cell_number, p.name, p.article, pwc.quantity_stored
        FROM part_warehouse_cell pwc
        JOIN part p ON pwc.part_id = p.id_part
        WHERE pwc.quantity_stored > 0
        ORDER BY pwc.cell_number ASC
    ''').fetchall()
    total_parts = 0
    for row in parts:
        cw.writerow([row['cell_number'], row['name'], row['article'], row['quantity_stored']])
        total_parts += row['quantity_stored']
    cw.writerow([f'ИТОГО:;{len(parts)} ячеек;;{total_parts} шт.'])
    cw.writerow([])
    cw.writerow([])

    # 2. Наличие готовых изделий
    cw.writerow(['2. СПРАВКА О НАЛИЧИИ ГОТОВЫХ ИЗДЕЛИЙ'])
    cw.writerow(['Ячейка;Изделие;Количество (шт.)'])
    products = conn.execute('''
        SELECT pwc.cell_number, pt.name, pwc.quantity_stored
        FROM product_warehouse_cell pwc
        JOIN product_type pt ON pwc.product_type_id = pt.id_product_type
        WHERE pwc.quantity_stored > 0
        ORDER BY pwc.cell_number ASC
    ''').fetchall()
    total_products = 0
    for row in products:
        cw.writerow([row['cell_number'], row['name'], row['quantity_stored']])
        total_products += row['quantity_stored']
    cw.writerow([f'ИТОГО:;{len(products)} ячеек;{total_products} шт.'])
    cw.writerow([])
    cw.writerow([])

    # 3. Комплект деталей по изделиям (спецификации)
    cw.writerow(['3. СПРАВКА О КОМПЛЕКТЕ ДЕТАЛЕЙ (СПЕЦИФИКАЦИИ)'])
    cw.writerow(['Изделие;Деталь;Артикул;Количество в 1 изделии'])
    comps = conn.execute('''
        SELECT pt.name as product_name, p.name as part_name, p.article, pc.quantity
        FROM product_composition pc
        JOIN product_type pt ON pc.product_type_id = pt.id_product_type
        JOIN part p ON pc.part_id = p.id_part
        ORDER BY pt.name ASC, p.name ASC
    ''').fetchall()
    for row in comps:
        cw.writerow([row['product_name'], row['part_name'], row['article'], row['quantity']])
    cw.writerow([f'Всего записей в спецификациях: {len(comps)}'])
    cw.writerow([])
    cw.writerow([])

    # 4. Дефицит деталей под месячную программу
    plans = get_production_plans(conn)
    cw.writerow(['4. РАСЧЁТ ДЕФИЦИТА ДЕТАЛЕЙ ПОД МЕСЯЧНУЮ ПРОГРАММУ'])
    cw.writerow(['Программа выпуска (план):'])
    all_products = conn.execute('SELECT * FROM product_type').fetchall()
    for p in all_products:
        plan_qty = plans.get(str(p['id_product_type']), 0)
        cw.writerow([f'  • {p["name"]}: {plan_qty} шт.'])
    cw.writerow([])
    cw.writerow(['Деталь;Артикул;Требуется (шт.);Есть на складе (шт.);Дефицит (шт.)'])

    all_parts = conn.execute('SELECT * FROM part').fetchall()
    total_deficit = 0
    for part in all_parts:
        part_id = part['id_part']
        total_needed = 0
        for p in all_products:
            plan_qty = plans.get(str(p['id_product_type']), 0)
            if plan_qty > 0:
                comp = conn.execute('SELECT quantity FROM product_composition WHERE product_type_id = ? AND part_id = ?',
                                   (p['id_product_type'], part_id)).fetchone()
                if comp:
                    total_needed += plan_qty * comp['quantity']
        stock_row = conn.execute('SELECT SUM(quantity_stored) as total FROM part_warehouse_cell WHERE part_id = ?', (part_id,)).fetchone()
        in_stock = stock_row['total'] if stock_row['total'] else 0
        deficit = max(0, total_needed - in_stock)
        if total_needed > 0:
            cw.writerow([part['name'], part['article'], total_needed, in_stock, deficit])
            total_deficit += deficit

    cw.writerow([f'Общий дефицит по программе: {total_deficit} шт.'])
    cw.writerow([])
    cw.writerow([])

    # Подвал
    cw.writerow(['=' * 80])
    cw.writerow([f'Отчёт сформирован: {now.strftime("%d.%m.%Y %H:%M")}'])
    cw.writerow(['ООО «ТехноПромСборка» — Система учета сборочного производства'])

    conn.close()
    output = Response(si.getvalue())
    output.headers['Content-Disposition'] = f'attachment; filename=kompleksny_otchet_{now.strftime("%Y%m%d")}.csv'
    output.headers['Content-type'] = 'text/csv; charset=utf-8-sig'
    return output

# ================= 12. КОМПЛЕКСНЫЙ ОТЧЁТ WORD =================
@app.route('/export_report_docx')
def export_report_docx():
    conn = get_db_connection()

    parts_stock = conn.execute('''
        SELECT pwc.cell_number, p.name, p.article, pwc.quantity_stored
        FROM part_warehouse_cell pwc
        JOIN part p ON pwc.part_id = p.id_part
        WHERE pwc.quantity_stored > 0
        ORDER BY pwc.cell_number ASC
    ''').fetchall()

    products_stock = conn.execute('''
        SELECT pwc.cell_number, pt.name, pwc.quantity_stored
        FROM product_warehouse_cell pwc
        JOIN product_type pt ON pwc.product_type_id = pt.id_product_type
        WHERE pwc.quantity_stored > 0
        ORDER BY pwc.cell_number ASC
    ''').fetchall()

    compositions = conn.execute('''
        SELECT pt.name as product_name, p.name as part_name, p.article, pc.quantity
        FROM product_composition pc
        JOIN product_type pt ON pc.product_type_id = pt.id_product_type
        JOIN part p ON pc.part_id = p.id_part
        ORDER BY pt.name ASC, p.name ASC
    ''').fetchall()

    plans = get_production_plans(conn)
    all_products = conn.execute('SELECT * FROM product_type').fetchall()
    all_parts = conn.execute('SELECT * FROM part').fetchall()

    production_plan = [(p['name'], plans.get(str(p['id_product_type']), 0)) for p in all_products]

    deficit_data = []
    for part in all_parts:
        part_id = part['id_part']
        total_needed = 0
        for p in all_products:
            plan_qty = plans.get(str(p['id_product_type']), 0)
            if plan_qty > 0:
                comp = conn.execute('SELECT quantity FROM product_composition WHERE product_type_id = ? AND part_id = ?',
                                   (p['id_product_type'], part_id)).fetchone()
                if comp:
                    total_needed += plan_qty * comp['quantity']
        stock_row = conn.execute('SELECT SUM(quantity_stored) as total FROM part_warehouse_cell WHERE part_id = ?', (part_id,)).fetchone()
        in_stock = stock_row['total'] if stock_row['total'] else 0
        if total_needed > 0:
            deficit_data.append({
                'part_name': part['name'],
                'article': part['article'],
                'total_needed': total_needed,
                'in_stock': in_stock,
                'deficit': max(0, total_needed - in_stock),
            })

    conn.close()
    now = datetime.now()
    fname = f'kompleksny_otchet_{now.strftime("%Y%m%d_%H%M")}.docx'
    out = doc_utils.build_full_report(parts_stock, products_stock, compositions, deficit_data, production_plan, fname)
    return send_file(out, as_attachment=True, download_name=f'Комплексный_отчёт_{now.strftime("%Y%m%d")}.docx')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
