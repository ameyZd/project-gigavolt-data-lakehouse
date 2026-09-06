DROP DATABASE IF EXISTS gigavolt_silver;

CREATE DATABASE gigavolt_silver
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE gigavolt_silver;

CREATE TABLE customers (
    customer_id VARCHAR(20) PRIMARY KEY,
    company_name VARCHAR(255) NOT NULL,
    contact_name VARCHAR(255) NOT NULL,
    phone VARCHAR(30),
    email VARCHAR(255),
    address VARCHAR(500),
    city VARCHAR(100),
    state CHAR(2),
    zip VARCHAR(20),
    country CHAR(2),
    created_date DATE,
    status VARCHAR(30)
) ENGINE=InnoDB;

CREATE TABLE equipment_units (
    equipment_id VARCHAR(20) PRIMARY KEY,
    customer_id VARCHAR(20) NOT NULL,
    model VARCHAR(100),
    serial_number VARCHAR(100),
    install_date DATE,
    capacity_kw DECIMAL(12,2),
    voltage VARCHAR(30),
    warranty_expiration DATE,
    status VARCHAR(30),
    location_notes TEXT,
    CONSTRAINT fk_equipment_customer
        FOREIGN KEY (customer_id)
        REFERENCES customers(customer_id)
) ENGINE=InnoDB;

CREATE TABLE parts_inventory (
    part_id VARCHAR(20) PRIMARY KEY,
    part_name VARCHAR(255) NOT NULL,
    category VARCHAR(100),
    manufacturer VARCHAR(150),
    supplier VARCHAR(150),
    unit_cost DECIMAL(12,2),
    unit_price DECIMAL(12,2),
    stock_quantity INT,
    reorder_level INT
) ENGINE=InnoDB;

CREATE TABLE service_logs (
    dispatch_id VARCHAR(20) PRIMARY KEY,
    equipment_id VARCHAR(20) NOT NULL,
    dispatch_date DATE,
    technician VARCHAR(255),
    service_type VARCHAR(100),
    hours_spent DECIMAL(10,2),
    labor_cost DECIMAL(12,2),
    resolution_summary TEXT,
    status VARCHAR(30),
    CONSTRAINT fk_service_equipment
        FOREIGN KEY (equipment_id)
        REFERENCES equipment_units(equipment_id)
) ENGINE=InnoDB;

CREATE TABLE warranty_claims (
    claim_id VARCHAR(20) PRIMARY KEY,
    equipment_id VARCHAR(20) NOT NULL,
    dispatch_id VARCHAR(20) NULL,
    part_id VARCHAR(20) NULL,
    claim_date DATE,
    claim_amount DECIMAL(12,2),
    approved_amount DECIMAL(12,2),
    status VARCHAR(30),
    denial_reason TEXT,
    claim_notes TEXT,
    CONSTRAINT fk_claim_equipment
        FOREIGN KEY (equipment_id)
        REFERENCES equipment_units(equipment_id),
    CONSTRAINT fk_claim_dispatch
        FOREIGN KEY (dispatch_id)
        REFERENCES service_logs(dispatch_id),
    CONSTRAINT fk_claim_part
        FOREIGN KEY (part_id)
        REFERENCES parts_inventory(part_id)
) ENGINE=InnoDB;
