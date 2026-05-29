import sqlite3

connection = sqlite3.connect('database.db')
cursor = connection.cursor()

cursor.executescript('''
    DROP TABLE IF EXISTS part_supply;
    DROP TABLE IF EXISTS client_order;
    DROP TABLE IF EXISTS product_composition;
    DROP TABLE IF EXISTS part_warehouse_cell;
    DROP TABLE IF EXISTS product_warehouse_cell;
    DROP TABLE IF EXISTS part;
    DROP TABLE IF EXISTS part_category;
    DROP TABLE IF EXISTS product_type;
    DROP TABLE IF EXISTS production_plan;
    DROP TABLE IF EXISTS supplier;

    -- 1. Категории деталей
    CREATE TABLE part_category (
        id_category INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE
    );

    -- 2. Детали
    CREATE TABLE part (
        id_part INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        article TEXT NOT NULL UNIQUE,
        category_id INTEGER,
        FOREIGN KEY (category_id) REFERENCES part_category (id_category) ON DELETE SET NULL
    );

    -- 3. Поставщики
    CREATE TABLE supplier (
        id_supplier INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        contact_info TEXT
    );

    -- 4. Поставки деталей (incoming shipments)
    CREATE TABLE part_supply (
        id_supply INTEGER PRIMARY KEY AUTOINCREMENT,
        supplier_id INTEGER,
        part_id INTEGER,
        quantity INTEGER NOT NULL,
        price REAL NOT NULL,
        supply_date TEXT NOT NULL,
        FOREIGN KEY (supplier_id) REFERENCES supplier (id_supplier) ON DELETE CASCADE,
        FOREIGN KEY (part_id) REFERENCES part (id_part) ON DELETE CASCADE
    );

    -- 5. Типы изделий
    CREATE TABLE product_type (
        id_product_type INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        description TEXT
    );

    -- 6. Состав изделия (спецификация)
    CREATE TABLE product_composition (
        product_type_id INTEGER,
        part_id INTEGER,
        quantity INTEGER NOT NULL,
        PRIMARY KEY (product_type_id, part_id),
        FOREIGN KEY (product_type_id) REFERENCES product_type (id_product_type) ON DELETE CASCADE,
        FOREIGN KEY (part_id) REFERENCES part (id_part) ON DELETE CASCADE
    );

    -- 7. Ячейки склада деталей
    CREATE TABLE part_warehouse_cell (
        id_part_cell INTEGER PRIMARY KEY AUTOINCREMENT,
        cell_number TEXT NOT NULL,
        part_id INTEGER,
        quantity_stored INTEGER DEFAULT 0,
        FOREIGN KEY (part_id) REFERENCES part (id_part) ON DELETE CASCADE,
        UNIQUE(cell_number, part_id)
    );

    -- 8. Ячейки склада изделий
    CREATE TABLE product_warehouse_cell (
        id_product_cell INTEGER PRIMARY KEY AUTOINCREMENT,
        cell_number TEXT NOT NULL,
        product_type_id INTEGER,
        quantity_stored INTEGER DEFAULT 0,
        FOREIGN KEY (product_type_id) REFERENCES product_type (id_product_type) ON DELETE CASCADE,
        UNIQUE(cell_number, product_type_id)
    );

    -- 9. План выпуска изделий
    CREATE TABLE production_plan (
        product_type_id INTEGER PRIMARY KEY,
        target_quantity INTEGER DEFAULT 0,
        FOREIGN KEY (product_type_id) REFERENCES product_type (id_product_type) ON DELETE CASCADE
    );

    -- 10. Заказы клиентов
    CREATE TABLE client_order (
        id_order INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_name TEXT NOT NULL,
        product_type_id INTEGER,
        quantity INTEGER NOT NULL,
        order_date TEXT NOT NULL,
        status TEXT DEFAULT 'В обработке',
        FOREIGN KEY (product_type_id) REFERENCES product_type (id_product_type) ON DELETE CASCADE
    );
''')

# Наполнение начальными тестовыми данными
cursor.executescript('''
    -- Категории
    INSERT INTO part_category (name) VALUES ('Процессоры'), ('Корпуса'), ('Материнские платы'), ('Оперативная память'), ('Видеокарты'), ('Блоки питания');
    
    -- Детали
    INSERT INTO part (name, article, category_id) VALUES 
    ('Intel Core i5-12400', 'CPU-I5-12', 1),
    ('AMD Ryzen 5 5600X', 'CPU-R5-56', 1),
    ('Deepcool Matrexx 55', 'CASE-DC-55', 2),
    ('ASUS Prime B660M', 'MB-AS-B66', 3),
    ('Gigabyte B550M DS3H', 'MB-GB-B55', 3),
    ('Kingston Fury 8GB DDR4', 'RAM-KF-8G', 4),
    ('NVIDIA RTX 4060 8GB', 'GPU-RTX-4060', 5),
    ('be quiet! System Power 9 600W', 'PSU-BQ-600', 6);
    
    -- Поставщики
    INSERT INTO supplier (name, contact_info) VALUES 
    ('ООО ДИСТРИБЬЮТ-ИТ', 'sales@distribut-it.ru'),
    ('АО ТЕХНОПОРТ', 'info@technoport.ru');
    
    -- Поставки
    INSERT INTO part_supply (supplier_id, part_id, quantity, price, supply_date) VALUES 
    (1, 1, 50, 12000.0, '2026-05-15'),
    (1, 6, 100, 2200.0, '2026-05-15'),
    (2, 4, 40, 8500.0, '2026-05-18'),
    (2, 6, 80, 3200.0, '2026-05-18'),
    (2, 7, 20, 35000.0, '2026-05-20');
    
    -- Изделия
    INSERT INTO product_type (name, description) VALUES 
    ('ПК Офисный Standard', 'Базовый компьютер для офисных задач'),
    ('ПК Игровой Advance', 'Производительный ПК для современных игр');
    
    -- Спецификации (составы)
    -- ПК Офисный: i5-12400 (1 шт), ASUS Prime (1 шт), RAM 8GB (1 шт), be quiet 600W (1 шт)
    INSERT INTO product_composition (product_type_id, part_id, quantity) VALUES 
    (1, 1, 1), (1, 4, 1), (1, 6, 1), (1, 8, 1);
    
    -- ПК Игровой: Ryzen 5 (1 шт), Gigabyte B550 (1 шт), RAM 8GB (2 шт), RTX 4060 (1 шт), be quiet 600W (1 шт)
    INSERT INTO product_composition (product_type_id, part_id, quantity) VALUES 
    (2, 2, 1), (2, 5, 1), (2, 6, 2), (2, 7, 1), (2, 8, 1);
    
    -- Ячейки склада деталей
    INSERT INTO part_warehouse_cell (cell_number, part_id, quantity_stored) VALUES 
    ('A-101', 1, 15),
    ('A-102', 2, 10),
    ('A-103', 4, 12),
    ('A-104', 3, 0), 
    ('A-105', 5, 8),
    ('A-106', 6, 32),
    ('A-107', 7, 5),
    ('A-108', 8, 12);
    
    -- Ячейки склада готовых изделий
    INSERT INTO product_warehouse_cell (cell_number, product_type_id, quantity_stored) VALUES 
    ('B-201', 1, 3),
    ('B-202', 2, 1);
    
    -- Планы выпуска
    INSERT INTO production_plan (product_type_id, target_quantity) VALUES 
    (1, 10), (2, 5);
    
    -- Заказы клиентов
    INSERT INTO client_order (customer_name, product_type_id, quantity, order_date, status) VALUES 
    ('ООО Ромашка', 1, 5, '2026-05-28', 'В обработке'),
    ('ИП Петров', 2, 2, '2026-05-29', 'В обработке'),
    ('АО Сокол', 1, 2, '2026-05-29', 'Собран');
''')

connection.commit()
connection.close()
print("База данных расширена до 10 таблиц с более детальной структурой!")
