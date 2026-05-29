import sqlite3
from werkzeug.security import generate_password_hash

connection = sqlite3.connect('database.db')
cursor = connection.cursor()

cursor.executescript('''
    DROP TABLE IF EXISTS client_order;
    DROP TABLE IF EXISTS user;
    DROP TABLE IF EXISTS product_composition;
    DROP TABLE IF EXISTS part_warehouse_cell;
    DROP TABLE IF EXISTS product_warehouse_cell;
    DROP TABLE IF EXISTS part;
    DROP TABLE IF EXISTS product_type;

    -- 1. Детали
    CREATE TABLE part (
        id_part INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        article TEXT NOT NULL UNIQUE
    );

    -- 2. Типы изделий
    CREATE TABLE product_type (
        id_product_type INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        description TEXT
    );

    -- 3. Состав изделия (спецификация)
    CREATE TABLE product_composition (
        product_type_id INTEGER,
        part_id INTEGER,
        quantity INTEGER NOT NULL,
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
        FOREIGN KEY (part_id) REFERENCES part (id_part) ON DELETE CASCADE,
        UNIQUE(cell_number, part_id)
    );

    -- 5. Ячейки склада готовых изделий
    CREATE TABLE product_warehouse_cell (
        id_product_cell INTEGER PRIMARY KEY AUTOINCREMENT,
        cell_number TEXT NOT NULL,
        product_type_id INTEGER,
        quantity_stored INTEGER DEFAULT 0,
        FOREIGN KEY (product_type_id) REFERENCES product_type (id_product_type) ON DELETE CASCADE,
        UNIQUE(cell_number, product_type_id)
    );

    -- 6. Пользователи (Авторизация)
    CREATE TABLE user (
        id_user INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('worker', 'client'))
    );

    -- 7. Заказы клиентов
    CREATE TABLE client_order (
        id_order INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        product_type_id INTEGER,
        quantity INTEGER NOT NULL,
        order_date TEXT NOT NULL,
        status TEXT DEFAULT 'В обработке',
        FOREIGN KEY (user_id) REFERENCES user (id_user) ON DELETE CASCADE,
        FOREIGN KEY (product_type_id) REFERENCES product_type (id_product_type) ON DELETE CASCADE
    );
''')

# Хешируем дефолтные пароли для демо-пользователей
worker_hash = generate_password_hash('admin')
client_hash = generate_password_hash('client')

# Наполнение тестовыми данными
cursor.execute('INSERT INTO user (username, password_hash, role) VALUES (?, ?, ?)', ('admin', worker_hash, 'worker'))
cursor.execute('INSERT INTO user (username, password_hash, role) VALUES (?, ?, ?)', ('client', client_hash, 'client'))

cursor.executescript('''
    -- 1. Детали (16 штук)
    INSERT INTO part (id_part, name, article) VALUES 
    (1, 'Intel Core i5-12400', 'CPU-I5-12'),
    (2, 'AMD Ryzen 5 5600X', 'CPU-R5-56'),
    (3, 'Intel Core i7-13700K', 'CPU-I7-13'),
    (4, 'AMD Ryzen 9 7900X', 'CPU-R9-79'),
    (5, 'ASUS Prime B660M', 'MB-AS-B66'),
    (6, 'Gigabyte B550M DS3H', 'MB-GB-B55'),
    (7, 'MSI PRO Z790-A WiFi', 'MB-MS-Z79'),
    (8, 'Kingston Fury 8GB DDR4', 'RAM-KF-8G'),
    (9, 'Kingston Fury 16GB DDR5', 'RAM-KF-16G'),
    (10, 'NVIDIA RTX 4060 8GB', 'GPU-RTX-4060'),
    (11, 'NVIDIA RTX 4080 16GB', 'GPU-RTX-4080'),
    (12, 'SSD Samsung 980 Pro 1TB', 'SSD-SM-980'),
    (13, 'be quiet! System Power 9 600W', 'PSU-BQ-600'),
    (14, 'Corsair RM850x 850W', 'PSU-CS-850'),
    (15, 'Deepcool Matrexx 55', 'CASE-DC-55'),
    (16, 'Fractal Design Meshify 2', 'CASE-FD-M2');
    
    -- 2. Типы изделий (5 штук)
    INSERT INTO product_type (id_product_type, name, description) VALUES 
    (1, 'ПК Офисный Standard', 'Базовый компьютер для офисных и повседневных задач'),
    (2, 'ПК Игровой Advance', 'Производительный компьютер для современных игр в 1080p'),
    (3, 'ПК Игровой Extreme Ultra', 'Флагманский геймерский ПК для игр в 4K и работы с графикой'),
    (4, 'Сервер начального уровня Pro', 'Сервер для малого бизнеса, баз данных и локальных сетей'),
    (5, 'Компактный Неттоп Nano', 'Ультракомпактное решение для медиацентров и тонких клиентов');
    
    -- 3. Составы изделий (спецификации)
    -- ПК Офисный
    INSERT INTO product_composition (product_type_id, part_id, quantity) VALUES 
    (1, 1, 1), (1, 5, 1), (1, 8, 1), (1, 12, 1), (1, 13, 1), (1, 15, 1);
    
    -- ПК Игровой Advance
    INSERT INTO product_composition (product_type_id, part_id, quantity) VALUES 
    (2, 2, 1), (2, 6, 1), (2, 8, 2), (2, 10, 1), (2, 12, 1), (2, 13, 1), (2, 15, 1);
    
    -- ПК Игровой Extreme Ultra
    INSERT INTO product_composition (product_type_id, part_id, quantity) VALUES 
    (3, 3, 1), (3, 7, 1), (3, 9, 2), (3, 11, 1), (3, 12, 2), (3, 14, 1), (3, 16, 1);
    
    -- Сервер начального уровня Pro
    INSERT INTO product_composition (product_type_id, part_id, quantity) VALUES 
    (4, 4, 1), (4, 6, 1), (4, 9, 4), (4, 12, 4), (4, 14, 1), (4, 16, 1);
    
    -- Компактный Неттоп Nano
    INSERT INTO product_composition (product_type_id, part_id, quantity) VALUES 
    (5, 1, 1), (5, 5, 1), (5, 8, 1), (5, 12, 1);
    
    -- 4. Ячейки склада деталей (адресное хранение)
    INSERT INTO part_warehouse_cell (cell_number, part_id, quantity_stored) VALUES 
    ('A-101', 1, 24),
    ('A-102', 2, 18),
    ('A-103', 3, 8),
    ('A-104', 4, 5),
    ('A-105', 5, 15),
    ('A-106', 6, 12),
    ('A-107', 7, 6),
    ('A-108', 8, 48),
    ('A-109', 9, 32),
    ('A-110', 10, 10),
    ('A-111', 11, 4),
    ('A-112', 12, 50),
    ('A-113', 13, 20),
    ('A-114', 14, 12),
    ('A-115', 15, 15),
    ('A-116', 16, 8),
    ('A-201', 1, 10),
    ('A-208', 8, 20);
    
    -- 5. Ячейки склада готовых изделий
    INSERT INTO product_warehouse_cell (cell_number, product_type_id, quantity_stored) VALUES 
    ('B-201', 1, 5),
    ('B-202', 2, 3),
    ('B-203', 3, 1),
    ('B-204', 4, 0),
    ('B-205', 5, 2);

    -- 7. Заказы клиентов (тестовые)
    INSERT INTO client_order (user_id, product_type_id, quantity, order_date, status) VALUES
    (2, 1, 2, '2026-05-29', 'В обработке'),
    (2, 2, 1, '2026-05-29', 'Собран');
''')

connection.commit()
connection.close()
print("База данных на dev-branch инициализирована (7 таблиц с пользователями и заказами).")
