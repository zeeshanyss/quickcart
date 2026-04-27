# QuickCart — Production E-Commerce System

A full-stack e-commerce application built on the research paper:
**"QuickCart: Design and Implementation of an Intelligent Web-Based E-Commerce System"**
— Mohammad Zeeshan, IILM University

## Tech Stack
- **Backend:** Python 3.10 + Flask 2.3 (MVC pattern)
- **Database:** SQLite (dev) / MySQL 8.0 compatible schema (prod)
- **Auth:** Flask-Login + bcrypt (cost factor 12)
- **Cart:** UUID-based server-side session persistence
- **Frontend:** HTML5 + CSS3 + Vanilla JS

## Features Implemented
1. **UUID Cart Persistence** — Cart keyed to UUID token, survives logout, tab close, session expiry
2. **Guest → Auth Cart Merge** — Atomic merge on login
3. **Server-side Price Calculation** — No client-side price trust
4. **RBAC** — Admin / Reseller / Customer roles
5. **Multi-mode Payment Sandbox** — UPI, Card, Net Banking (no card data stored)
6. **PCI-DSS Design** — Only transaction reference ID stored
7. **Bulk CSV Upload** — Smart upsert (create or update by SKU)
8. **Automated Stock Logging** — Every change logged with reason
9. **Reseller Dashboard** — Bulk profit calculator with custom margins
10. **Order Lifecycle** — pending → confirmed → shipped → delivered

## Setup

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run the app (auto-seeds demo data)
python app.py

# 3. Open browser
# http://localhost:5000
```

## Demo Accounts

| Role     | Email                       | Password      |
|----------|-----------------------------|---------------|
| Admin    | admin@quickcart.com         | admin123      |
| Reseller | reseller@quickcart.com      | reseller123   |
| Customer | customer@quickcart.com      | customer123   |

## URL Routes

| Route                   | Access       | Description                      |
|------------------------|--------------|----------------------------------|
| /                      | Public       | Homepage with featured products  |
| /shop                  | Public       | Product listing with search/filter|
| /product/<id>          | Public       | Product detail page              |
| /cart                  | Public       | Cart (UUID-persisted)            |
| /checkout              | Logged in    | Multi-step checkout              |
| /orders                | Logged in    | Order history                    |
| /reseller              | Reseller+    | Bulk profit calculator           |
| /admin                 | Admin only   | Dashboard                        |
| /admin/products        | Admin only   | CRUD products                    |
| /admin/bulk-upload     | Admin only   | CSV bulk import/update           |
| /admin/orders          | Admin only   | Manage + update order status     |
| /admin/users           | Admin only   | Manage user roles & discounts    |
| /admin/stock-log       | Admin only   | Automated stock audit trail      |

## Bulk Upload CSV Format

Download template from `/admin/bulk-upload/template`

```
name,description,price,stock,category,sku,image_url
Wireless Earbuds,Great sound,2999,50,Electronics,SKU001,https://...
Cotton T-Shirt,Comfortable,599,100,Clothing,SKU002,
```

- **SKU match** → updates existing product
- **New SKU / no SKU** → creates new product
- All stock changes auto-logged

## Security (from paper)
- bcrypt password hashing (cost factor 12)
- TLS 1.3 in production (configure nginx/gunicorn)
- Prepared statements via SQLAlchemy ORM
- RBAC on all admin endpoints
- No cardholder data stored — only transaction reference ID

## Production Deployment

```bash
# Use gunicorn + nginx
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:8000 app:app

# Switch database URI in app.py to MySQL:
# SQLALCHEMY_DATABASE_URI = 'mysql+pymysql://user:pass@host/quickcart'
```
