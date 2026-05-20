from flask import Flask, render_template, request, redirect, url_for
import sqlite3

app = Flask(__name__)

def get_db_connection():
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/')
def index():
    return render_template('index.html')

# ================= 1. ТАБЛИЦА PART =================
@app.route('/parts', methods=['GET', 'POST'])
def parts():
    conn = get_db_connection()
    if request.method == 'POST':
        conn.execute('INSERT INTO part (name, article) VALUES (?, ?)', 
                     (request.form['name'], request.form['article']))
        conn.commit()
        return redirect(url_for('parts'))
    data = conn.execute('SELECT * FROM part').fetchall()
    conn.close()
    return render_template('parts.html', data=data)

@app.route('/delete_part/<int:id>')
def delete_part(id):
    conn = get_db_connection()
    conn.execute('DELETE FROM part WHERE id_part = ?', (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('parts'))

# ================= 2. ТАБЛИЦА PRODUCT_TYPE =================
@app.route('/products', methods=['GET', 'POST'])
def products():
    conn = get_db_connection()
    if request.method == 'POST':
        conn.execute('INSERT INTO product_type (name, description) VALUES (?, ?)', 
                     (request.form['name'], request.form['description']))
        conn.commit()
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
    return redirect(url_for('products'))

# ================= 3. ТАБЛИЦА PRODUCT_COMPOSITION =================
@app.route('/compositions', methods=['GET', 'POST'])
def compositions():
    conn = get_db_connection()
    if request.method == 'POST':
        conn.execute('INSERT INTO product_composition (product_type_id, part_id, quantity) VALUES (?, ?, ?)', 
                     (request.form['product_type_id'], request.form['part_id'], request.form['quantity']))
        conn.commit()
        return redirect(url_for('compositions'))
    
    data = conn.execute('''
        SELECT c.*, p.name as product_name, d.name as part_name 
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
    return redirect(url_for('compositions'))

# ================= 4. ТАБЛИЦА PART_WAREHOUSE_CELL =================
@app.route('/part_cells', methods=['GET', 'POST'])
def part_cells():
    conn = get_db_connection()
    if request.method == 'POST':
        conn.execute('INSERT INTO part_warehouse_cell (cell_number, part_id, quantity_stored) VALUES (?, ?, ?)', 
                     (request.form['cell_number'], request.form['part_id'], request.form['quantity_stored']))
        conn.commit()
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
    return redirect(url_for('part_cells'))

# ================= 5. ТАБЛИЦА PRODUCT_WAREHOUSE_CELL =================
@app.route('/product_cells', methods=['GET', 'POST'])
def product_cells():
    conn = get_db_connection()
    if request.method == 'POST':
        conn.execute('INSERT INTO product_warehouse_cell (cell_number, product_type_id, quantity_stored) VALUES (?, ?, ?)', 
                     (request.form['cell_number'], request.form['product_type_id'], request.form['quantity_stored']))
        conn.commit()
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
    return redirect(url_for('product_cells'))

if __name__ == '__main__':
    app.run(debug=True)
