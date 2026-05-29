import sqlite3

connection = sqlite3.connect('database.db')
cursor = connection.cursor()

cursor.executescript('''
    DROP TABLE IF EXISTS product_composition;
    DROP TABLE IF EXISTS part_warehouse_cell;
    DROP TABLE IF EXISTS product_warehouse_cell;
    DROP TABLE IF EXISTS part;
    DROP TABLE IF EXISTS product_type;
    DROP TABLE IF EXISTS production_plan;

    -- 1. Таблица деталей
    CREATE TABLE part (
        id_part INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        article TEXT NOT NULL
    );

    -- 2. Таблица типов изделий
    CREATE TABLE product_type (
        id_product_type INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT
    );

    -- 3. Состав изделия
    CREATE TABLE product_composition (
        product_type_id INTEGER,
        part_id INTEGER,
        quantity INTEGER,
        PRIMARY KEY (product_type_id, part_id),
        FOREIGN KEY (product_type_id) REFERENCES product_type (id_product_type) ON DELETE CASCADE,
        FOREIGN KEY (part_id) REFERENCES part (id_part) ON DELETE CASCADE
    );

    -- 4. Ячейки склада деталей
    CREATE TABLE part_warehouse_cell (
        id_part_cell INTEGER PRIMARY KEY AUTOINCREMENT,
        cell_number TEXT NOT NULL,
        part_id INTEGER,
        quantity_stored INTEGER DEFAULT 0,
        FOREIGN KEY (part_id) REFERENCES part (id_part) ON DELETE CASCADE
    );

    -- 5. Ячейки склада изделий
    CREATE TABLE product_warehouse_cell (
        id_product_cell INTEGER PRIMARY KEY AUTOINCREMENT,
        cell_number TEXT NOT NULL,
        product_type_id INTEGER,
        quantity_stored INTEGER DEFAULT 0,
        FOREIGN KEY (product_type_id) REFERENCES product_type (id_product_type) ON DELETE CASCADE
    );

    -- 6. План выпуска изделий
    CREATE TABLE production_plan (
        product_type_id INTEGER PRIMARY KEY,
        target_quantity INTEGER DEFAULT 0,
        FOREIGN KEY (product_type_id) REFERENCES product_type (id_product_type) ON DELETE CASCADE
    );
''')

# Добавим немного базовых данных для старта
cursor.executescript('''
    INSERT INTO part (name, article) VALUES ('Процессор', 'CPU-01'), ('Корпус', 'CASE-ATX'), ('Материнская плата', 'MB-LGA1700'), ('Оперативная память', 'RAM-DDR4-8G');
    INSERT INTO product_type (name, description) VALUES ('ПК Офисный', 'Стандартный рабочий компьютер'), ('ПК Игровой', 'Мощная игровая станция');
    
    -- Спецификации
    INSERT INTO product_composition (product_type_id, part_id, quantity) VALUES (1, 1, 1), (1, 2, 1), (1, 3, 1), (1, 4, 1);
    INSERT INTO product_composition (product_type_id, part_id, quantity) VALUES (2, 1, 1), (2, 2, 1), (2, 3, 1), (2, 4, 2);
    
    -- Остатки на складе деталей
    INSERT INTO part_warehouse_cell (cell_number, part_id, quantity_stored) VALUES ('A-1', 1, 10), ('A-2', 2, 8), ('A-3', 3, 15), ('A-4', 4, 20);
    
    -- Остатки на складе изделий
    INSERT INTO product_warehouse_cell (cell_number, product_type_id, quantity_stored) VALUES ('B-1', 1, 3);
    
    -- Планы выпуска
    INSERT INTO production_plan (product_type_id, target_quantity) VALUES (1, 15), (2, 5);
''')

connection.commit()
connection.close()
print("БД с таблицей планов успешно создана!")
