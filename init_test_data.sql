-- 电商智能客服系统测试数据（可直接执行）
-- 说明：
-- 1) 本脚本会确保数据库/表存在（CREATE DATABASE / CREATE TABLE IF NOT EXISTS）
-- 2) 默认会删除 id 以 ORD2024 开头的历史测试数据，再重新插入
-- 3) 统一使用 utf8mb4，避免中文乱码

SET NAMES utf8mb4;
SET time_zone = '+00:00';

CREATE DATABASE IF NOT EXISTS ecommerce_cs
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_0900_ai_ci;

USE ecommerce_cs;

-- 建表（若已存在则跳过）
CREATE TABLE IF NOT EXISTS orders (
  id VARCHAR(32) NOT NULL COMMENT '订单ID',
  user_id VARCHAR(32) NOT NULL COMMENT '用户ID',
  product_name VARCHAR(255) NOT NULL COMMENT '商品名称',
  product_id VARCHAR(32) NOT NULL COMMENT '商品ID',
  amount DECIMAL(10,2) NOT NULL COMMENT '订单金额',
  status VARCHAR(32) NOT NULL COMMENT '订单状态：paid/shipped/delivered/refunding/refunded/cancelled 等',
  payment_method VARCHAR(32) NULL COMMENT '支付方式',
  shipping_address VARCHAR(512) NOT NULL COMMENT '收货地址',
  tracking_number VARCHAR(64) NULL COMMENT '物流单号',
  created_at DATETIME NOT NULL COMMENT '创建时间',
  paid_at DATETIME NULL COMMENT '支付时间',
  shipped_at DATETIME NULL COMMENT '发货时间',
  delivered_at DATETIME NULL COMMENT '签收时间',
  refund_amount DECIMAL(10,2) NULL COMMENT '退款金额',
  refund_status VARCHAR(32) NULL COMMENT '退款状态：pending/processing/completed/rejected 等',
  refund_reason VARCHAR(255) NULL COMMENT '退款原因',
  reject_reason VARCHAR(255) NULL COMMENT '拒绝原因',
  estimated_refund_date DATETIME NULL COMMENT '预计退款日期',
  PRIMARY KEY (id),
  KEY idx_user_id (user_id),
  KEY idx_status (status),
  KEY idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 清理现有测试数据（只清理本脚本生成的 ORD2024*）
DELETE FROM orders WHERE id LIKE 'ORD2024%';

-- 插入测试订单数据
INSERT INTO orders (
    id, user_id, product_name, product_id, amount, status,
    payment_method, shipping_address, tracking_number,
    created_at, paid_at, shipped_at, delivered_at,
    refund_amount, refund_status, refund_reason, reject_reason, estimated_refund_date
) VALUES
-- 1. 已支付待发货订单
('ORD20240101001', 'USER001', 'iPhone 15 Pro Max 256GB 钛金色', 'PROD001', 8999.00, 'paid',
 '支付宝', '北京市朝阳区建国路88号SOHO现代城A座1001室', NULL,
 '2024-01-01 10:30:00', '2024-01-01 10:32:00', NULL, NULL,
 NULL, NULL, NULL, NULL, NULL),

-- 2. 已发货在途订单
('ORD20240102001', 'USER002', '小米14 Ultra 16GB+512GB 黑色', 'PROD002', 6499.00, 'shipped',
 '微信支付', '上海市浦东新区陆家嘴环路1000号恒生银行大厦20楼', 'SF1234567890',
 '2024-01-02 14:20:00', '2024-01-02 14:22:00', '2024-01-03 09:00:00', NULL,
 NULL, NULL, NULL, NULL, NULL),

-- 3. 已签收订单
('ORD20240103001', 'USER003', 'MacBook Pro 14英寸 M3 Pro 18GB+512GB', 'PROD003', 15999.00, 'delivered',
 '支付宝', '广州市天河区珠江新城花城大道85号高德置地春广场', 'JD9876543210',
 '2024-01-03 09:15:00', '2024-01-03 09:17:00', '2024-01-04 10:00:00', '2024-01-06 16:30:00',
 NULL, NULL, NULL, NULL, NULL),

-- 4. 退款处理中订单
('ORD20240104001', 'USER004', 'AirPods Pro 2代 USB-C充电盒', 'PROD004', 1899.00, 'refunding',
 '微信支付', '深圳市南山区科技园南区深南大道9988号', 'YTO1122334455',
 '2024-01-04 11:00:00', '2024-01-04 11:02:00', '2024-01-05 08:00:00', '2024-01-07 10:00:00',
 1899.00, 'processing', '商品质量问题，耳机有杂音', NULL, '2024-01-15 00:00:00'),

-- 5. 退款已完成订单
('ORD20240105001', 'USER005', '华为Mate 60 Pro 12GB+512GB 雅川青', 'PROD005', 6999.00, 'refunded',
 '支付宝', '杭州市西湖区文三路90号东部软件园', 'SF6677889900',
 '2024-01-05 15:30:00', '2024-01-05 15:32:00', '2024-01-06 09:00:00', '2024-01-08 14:00:00',
 6999.00, 'completed', '不喜欢颜色，想换其他款式', NULL, NULL),

-- 6. 退款被拒绝订单
('ORD20240106001', 'USER006', 'iPad Air 5代 256GB 星光色', 'PROD006', 4799.00, 'delivered',
 '微信支付', '成都市高新区天府大道中段1366号天府软件园', 'EMS3344556677',
 '2024-01-06 10:00:00', '2024-01-06 10:02:00', '2024-01-07 11:00:00', '2024-01-09 15:00:00',
 NULL, 'rejected', '使用后不满意', '商品已激活使用超过7天，不符合退货条件', NULL),

-- 7. 已取消订单
('ORD20240107001', 'USER007', 'OPPO Find X7 Ultra 16GB+512GB 大漠银月', 'PROD007', 5999.00, 'cancelled',
 NULL, '武汉市洪山区光谷大道61号智慧园', NULL,
 '2024-01-07 16:45:00', NULL, NULL, NULL,
 NULL, NULL, NULL, NULL, NULL),

-- 8. 多件商品订单（已发货）
('ORD20240108001', 'USER001', 'Apple Watch Series 9 GPS 45mm 午夜色', 'PROD008', 3199.00, 'shipped',
 '支付宝', '北京市朝阳区建国路88号SOHO现代城A座1001室', 'SF2233445566',
 '2024-01-08 09:20:00', '2024-01-08 09:22:00', '2024-01-09 10:00:00', NULL,
 NULL, NULL, NULL, NULL, NULL),

-- 9. 大额订单（已签收）
('ORD20240109001', 'USER008', 'Sony A7M4 全画幅微单相机 + 24-70mm镜头套装', 'PROD009', 18999.00, 'delivered',
 '支付宝', '南京市玄武区中山东路300号长发中心', 'JD5566778899',
 '2024-01-09 13:00:00', '2024-01-09 13:05:00', '2024-01-10 08:00:00', '2024-01-12 11:00:00',
 NULL, NULL, NULL, NULL, NULL),

-- 10. 退款待审核订单
('ORD20240110001', 'USER009', 'DJI Mini 4 Pro 无人机 畅飞套装', 'PROD010', 4788.00, 'refunding',
 '微信支付', '西安市雁塔区科技路48号创业广场', 'YTO9988776655',
 '2024-01-10 14:30:00', '2024-01-10 14:32:00', '2024-01-11 09:00:00', '2024-01-13 16:00:00',
 4788.00, 'pending', '收到商品有划痕，疑似二手', NULL, NULL),

-- 11. 同一用户的第二个订单（已支付）
('ORD20240111001', 'USER002', 'Redmi K70 Pro 16GB+512GB 墨羽', 'PROD011', 3299.00, 'paid',
 '支付宝', '上海市浦东新区陆家嘴环路1000号恒生银行大厦20楼', NULL,
 '2024-01-11 10:00:00', '2024-01-11 10:02:00', NULL, NULL,
 NULL, NULL, NULL, NULL, NULL),

-- 12. 配件订单（已发货）
('ORD20240112001', 'USER003', 'Anker 氮化镓充电器 140W + USB-C数据线套装', 'PROD012', 399.00, 'shipped',
 '微信支付', '广州市天河区珠江新城花城大道85号高德置地春广场', 'SF4455667788',
 '2024-01-12 11:30:00', '2024-01-12 11:32:00', '2024-01-13 08:00:00', NULL,
 NULL, NULL, NULL, NULL, NULL);

-- 查看插入的数据
SELECT COUNT(*) as total_orders FROM orders;
SELECT status, COUNT(*) as count FROM orders GROUP BY status;
