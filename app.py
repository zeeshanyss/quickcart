"""
QuickCart - Production E-Commerce System
Flask + SQLite | UUID-based server-side cart persistence
Features: Auth, Smart Cart, Payments, Bulk Upload, Reseller Pricing, Admin Dashboard
         Multi-image upload per product with thumbnail generation
"""

from flask import (Flask, render_template, request, redirect, url_for,
                   session, flash, jsonify, make_response)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (LoginManager, UserMixin, login_user, logout_user,
                         login_required, current_user)
from flask_bcrypt import Bcrypt
from functools import wraps
from datetime import datetime
from PIL import Image as PILImage
import uuid, csv, io, json, os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'qc-secret-key-2025-change-in-prod'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///quickcart.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# ── Image upload config ──
UPLOAD_FOLDER    = os.path.join(os.path.dirname(__file__), 'static', 'uploads', 'products')
THUMB_FOLDER     = os.path.join(UPLOAD_FOLDER, 'thumbs')
ALLOWED_EXT      = {'png', 'jpg', 'jpeg', 'webp', 'gif'}
MAX_IMAGE_SIZE   = 5 * 1024 * 1024   # 5 MB per file
THUMB_SIZE       = (400, 400)
DISPLAY_SIZE     = (800, 800)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(THUMB_FOLDER,  exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

db = SQLAlchemy(app)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# ─────────────────────────── MODELS ───────────────────────────

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id            = db.Column(db.Integer, primary_key=True)
    name          = db.Column(db.String(120), nullable=False)
    email         = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role          = db.Column(db.String(20), default='customer')  # admin / customer / reseller
    reseller_discount = db.Column(db.Float, default=0.0)          # % discount for resellers
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    orders        = db.relationship('Order', backref='buyer', lazy=True)

class Category(db.Model):
    __tablename__ = 'categories'
    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(80), unique=True, nullable=False)
    products    = db.relationship('Product', backref='cat', lazy=True)

class Product(db.Model):
    __tablename__ = 'products'
    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    price       = db.Column(db.Float, nullable=False)
    stock       = db.Column(db.Integer, default=0)
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'))
    image_url   = db.Column(db.String(300), default='')   # kept for legacy / URL fallback
    sku         = db.Column(db.String(80), unique=True)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    is_active   = db.Column(db.Boolean, default=True)
    images      = db.relationship('ProductImage', backref='product',
                                  lazy=True, cascade='all, delete-orphan',
                                  order_by='ProductImage.sort_order')

    @property
    def primary_image_url(self):
        """Return the best available image URL for this product."""
        primary = next((img for img in self.images if img.is_primary), None)
        if not primary and self.images:
            primary = self.images[0]
        if primary:
            return url_for('static', filename=f'uploads/products/{primary.filename}')
        if self.image_url:
            return self.image_url
        return 'https://placehold.co/400x400/1a1a24/7a7a9a?text=No+Image'

    @property
    def primary_thumb_url(self):
        """Return thumbnail URL (400x400 cropped)."""
        primary = next((img for img in self.images if img.is_primary), None)
        if not primary and self.images:
            primary = self.images[0]
        if primary:
            thumb = f'uploads/products/thumbs/{primary.filename}'
            thumb_path = os.path.join(app.root_path, 'static', thumb)
            if os.path.exists(thumb_path):
                return url_for('static', filename=thumb)
        return self.primary_image_url

class ProductImage(db.Model):
    """Multiple uploaded images per product."""
    __tablename__ = 'product_images'
    id         = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    filename   = db.Column(db.String(200), nullable=False)
    is_primary = db.Column(db.Boolean, default=False)
    sort_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Cart(db.Model):
    __tablename__ = 'carts'
    id           = db.Column(db.Integer, primary_key=True)
    session_uuid = db.Column(db.String(36), unique=True, nullable=False)
    user_id      = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)
    items        = db.relationship('CartItem', backref='cart', lazy=True,
                                   cascade='all, delete-orphan')

class CartItem(db.Model):
    __tablename__ = 'cart_items'
    id         = db.Column(db.Integer, primary_key=True)
    cart_id    = db.Column(db.Integer, db.ForeignKey('carts.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    quantity   = db.Column(db.Integer, default=1)
    product    = db.relationship('Product')

class Order(db.Model):
    __tablename__ = 'orders'
    id             = db.Column(db.Integer, primary_key=True)
    order_ref      = db.Column(db.String(36), unique=True, default=lambda: str(uuid.uuid4())[:8].upper())
    user_id        = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    total          = db.Column(db.Float, nullable=False)
    tax            = db.Column(db.Float, default=0)
    delivery       = db.Column(db.Float, default=0)
    status         = db.Column(db.String(30), default='pending')
    payment_method = db.Column(db.String(30))
    payment_ref    = db.Column(db.String(80))
    address        = db.Column(db.Text)
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)
    items          = db.relationship('OrderItem', backref='order', lazy=True)

class OrderItem(db.Model):
    __tablename__ = 'order_items'
    id         = db.Column(db.Integer, primary_key=True)
    order_id   = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    quantity   = db.Column(db.Integer)
    price      = db.Column(db.Float)
    product    = db.relationship('Product')

class StockLog(db.Model):
    __tablename__ = 'stock_logs'
    id         = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    change     = db.Column(db.Integer)
    reason     = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    product    = db.relationship('Product')

# ─────────────────────────── HELPERS ───────────────────────────

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXT

def save_product_image(file_obj):
    """
    Save an uploaded image file:
    - Validates extension and size
    - Resizes to max 800x800 (keeps aspect ratio) for display
    - Creates a 400x400 thumbnail (cropped to square) for listings
    - Returns filename (UUID-based) or raises ValueError
    """
    if not file_obj or not file_obj.filename:
        raise ValueError('No file selected')
    if not allowed_file(file_obj.filename):
        raise ValueError(f'Invalid file type. Allowed: {", ".join(ALLOWED_EXT)}')

    # Read and check size
    file_bytes = file_obj.read()
    if len(file_bytes) > MAX_IMAGE_SIZE:
        raise ValueError(f'File too large. Max size is 5 MB')

    ext      = file_obj.filename.rsplit('.', 1)[1].lower()
    ext      = 'jpg' if ext == 'jpeg' else ext
    filename = f'{uuid.uuid4().hex}.{ext}'

    # Open with Pillow
    img = PILImage.open(io.BytesIO(file_bytes)).convert('RGB')

    # Save display image (max 800x800, keep ratio)
    img.thumbnail(DISPLAY_SIZE, PILImage.LANCZOS)
    img.save(os.path.join(UPLOAD_FOLDER, filename), optimize=True, quality=88)

    # Save thumbnail (400x400 square crop from center)
    thumb = PILImage.open(io.BytesIO(file_bytes)).convert('RGB')
    w, h  = thumb.size
    side  = min(w, h)
    left  = (w - side) // 2
    top   = (h - side) // 2
    thumb = thumb.crop((left, top, left + side, top + side))
    thumb = thumb.resize(THUMB_SIZE, PILImage.LANCZOS)
    thumb.save(os.path.join(THUMB_FOLDER, filename), optimize=True, quality=85)

    return filename

def delete_product_image_files(filename):
    """Remove image and its thumbnail from disk."""
    for path in [
        os.path.join(UPLOAD_FOLDER, filename),
        os.path.join(THUMB_FOLDER,  filename),
    ]:
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass

def get_or_create_cart():
    """UUID-based server-side cart — persists regardless of auth state."""
    if 'cart_uuid' not in session:
        session['cart_uuid'] = str(uuid.uuid4())
    cart = Cart.query.filter_by(session_uuid=session['cart_uuid']).first()
    if not cart:
        cart = Cart(session_uuid=session['cart_uuid'],
                    user_id=current_user.id if current_user.is_authenticated else None)
        db.session.add(cart)
        db.session.commit()
    return cart

def merge_guest_cart(user):
    """Merge guest UUID cart into authenticated user's cart on login."""
    if 'cart_uuid' not in session:
        return
    guest_cart = Cart.query.filter_by(session_uuid=session['cart_uuid']).first()
    if not guest_cart:
        return
    user_cart = Cart.query.filter_by(user_id=user.id).first()
    if user_cart and user_cart.id != guest_cart.id:
        for gi in guest_cart.items:
            existing = CartItem.query.filter_by(
                cart_id=user_cart.id, product_id=gi.product_id).first()
            if existing:
                existing.quantity += gi.quantity
            else:
                db.session.add(CartItem(cart_id=user_cart.id,
                                        product_id=gi.product_id,
                                        quantity=gi.quantity))
        db.session.delete(guest_cart)
        session['cart_uuid'] = user_cart.session_uuid
    else:
        guest_cart.user_id = user.id
    db.session.commit()

def calc_cart_totals(cart, user=None):
    """Server-side price calculation — never trusted from client."""
    subtotal = 0
    discount_pct = 0
    if user and user.is_authenticated and user.role == 'reseller':
        discount_pct = user.reseller_discount
    for item in cart.items:
        p = item.product
        price = p.price * (1 - discount_pct / 100)
        subtotal += price * item.quantity
    tax = round(subtotal * 0.18, 2)          # 18% GST
    delivery = 0 if subtotal >= 500 else 50  # Free above ₹500
    total = round(subtotal + tax + delivery, 2)
    return dict(subtotal=round(subtotal, 2), tax=tax,
                delivery=delivery, total=total, discount_pct=discount_pct)

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            flash('Admin access required.', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated

TAX_RATE = 0.18
FREE_DELIVERY_THRESHOLD = 500
DELIVERY_CHARGE = 50

# ─────────────────────────── PUBLIC ROUTES ───────────────────────────

@app.route('/')
def index():
    categories = Category.query.all()
    featured   = Product.query.filter_by(is_active=True).order_by(Product.id.desc()).limit(8).all()
    return render_template('index.html', categories=categories, featured=featured)

@app.route('/shop')
def shop():
    q       = request.args.get('q', '')
    cat_id  = request.args.get('cat', type=int)
    sort    = request.args.get('sort', 'newest')
    query   = Product.query.filter_by(is_active=True)
    if q:
        query = query.filter(Product.name.ilike(f'%{q}%'))
    if cat_id:
        query = query.filter_by(category_id=cat_id)
    if sort == 'price_asc':
        query = query.order_by(Product.price.asc())
    elif sort == 'price_desc':
        query = query.order_by(Product.price.desc())
    else:
        query = query.order_by(Product.id.desc())
    products   = query.all()
    categories = Category.query.all()
    return render_template('shop.html', products=products, categories=categories,
                           q=q, cat_id=cat_id, sort=sort)

@app.route('/product/<int:pid>')
def product_detail(pid):
    p = Product.query.get_or_404(pid)
    return render_template('product.html', product=p)

# ─────────────────────────── AUTH ───────────────────────────

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name  = request.form['name'].strip()
        email = request.form['email'].strip().lower()
        pw    = request.form['password']
        role  = request.form.get('role', 'customer')
        if User.query.filter_by(email=email).first():
            flash('Email already registered.', 'danger')
            return redirect(url_for('register'))
        pw_hash = bcrypt.generate_password_hash(pw).decode('utf-8')
        user = User(name=name, email=email, password_hash=pw_hash, role=role,
                    reseller_discount=10.0 if role == 'reseller' else 0.0)
        db.session.add(user)
        db.session.commit()
        flash('Account created! Please log in.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        pw    = request.form['password']
        user  = User.query.filter_by(email=email).first()
        if user and bcrypt.check_password_hash(user.password_hash, pw):
            login_user(user)
            merge_guest_cart(user)
            flash(f'Welcome back, {user.name}!', 'success')
            return redirect(request.args.get('next') or url_for('index'))
        flash('Invalid credentials.', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    session.pop('cart_uuid', None)
    return redirect(url_for('index'))

# ─────────────────────────── CART (Server-side) ───────────────────────────

@app.route('/cart')
def cart_view():
    cart   = get_or_create_cart()
    totals = calc_cart_totals(cart, current_user)
    return render_template('cart.html', cart=cart, totals=totals)

@app.route('/cart/add', methods=['POST'])
def cart_add():
    data       = request.get_json()
    product_id = int(data['product_id'])
    qty        = int(data.get('quantity', 1))
    product    = Product.query.get_or_404(product_id)
    cart       = get_or_create_cart()
    item = CartItem.query.filter_by(cart_id=cart.id, product_id=product_id).first()
    if item:
        item.quantity = min(item.quantity + qty, product.stock)
    else:
        db.session.add(CartItem(cart_id=cart.id, product_id=product_id,
                                quantity=min(qty, product.stock)))
    db.session.commit()
    totals = calc_cart_totals(cart, current_user)
    return jsonify(success=True, cart_count=len(cart.items), totals=totals)

@app.route('/cart/update', methods=['POST'])
def cart_update():
    data    = request.get_json()
    item_id = int(data['item_id'])
    qty     = int(data['quantity'])
    item    = CartItem.query.get_or_404(item_id)
    cart    = get_or_create_cart()
    if item.cart_id != cart.id:
        return jsonify(success=False, error='Unauthorized'), 403
    if qty <= 0:
        db.session.delete(item)
    else:
        item.quantity = min(qty, item.product.stock)
    db.session.commit()
    totals = calc_cart_totals(cart, current_user)
    return jsonify(success=True, totals=totals, cart_count=len(cart.items))

@app.route('/cart/remove/<int:item_id>', methods=['POST'])
def cart_remove(item_id):
    cart = get_or_create_cart()
    item = CartItem.query.get_or_404(item_id)
    if item.cart_id == cart.id:
        db.session.delete(item)
        db.session.commit()
    totals = calc_cart_totals(cart, current_user)
    return jsonify(success=True, totals=totals, cart_count=len(cart.items))

@app.route('/cart/count')
def cart_count():
    cart = get_or_create_cart()
    return jsonify(count=len(cart.items))

# ─────────────────────────── CHECKOUT ───────────────────────────

@app.route('/checkout', methods=['GET', 'POST'])
@login_required
def checkout():
    cart   = get_or_create_cart()
    totals = calc_cart_totals(cart, current_user)
    if not cart.items:
        flash('Your cart is empty.', 'warning')
        return redirect(url_for('cart_view'))
    if request.method == 'POST':
        address        = request.form['address'].strip()
        payment_method = request.form['payment_method']
        # Validate stock server-side
        for item in cart.items:
            if item.product.stock < item.quantity:
                flash(f'Insufficient stock for {item.product.name}.', 'danger')
                return redirect(url_for('checkout'))
        # Create order (no card data stored — only reference)
        payment_ref = 'TXN-' + str(uuid.uuid4())[:12].upper()
        order = Order(user_id=current_user.id,
                      total=totals['total'],
                      tax=totals['tax'],
                      delivery=totals['delivery'],
                      status='confirmed',
                      payment_method=payment_method,
                      payment_ref=payment_ref,
                      address=address)
        db.session.add(order)
        db.session.flush()
        for item in cart.items:
            discount = current_user.reseller_discount if current_user.role == 'reseller' else 0
            price = item.product.price * (1 - discount / 100)
            db.session.add(OrderItem(order_id=order.id, product_id=item.product_id,
                                     quantity=item.quantity, price=price))
            # Deduct stock + log
            item.product.stock -= item.quantity
            db.session.add(StockLog(product_id=item.product_id,
                                    change=-item.quantity,
                                    reason=f'Order {order.order_ref}'))
        # Clear cart
        for item in cart.items:
            db.session.delete(item)
        db.session.commit()
        flash(f'Order #{order.order_ref} placed! Payment ref: {payment_ref}', 'success')
        return redirect(url_for('order_detail', order_id=order.id))
    return render_template('checkout.html', cart=cart, totals=totals)

# ─────────────────────────── ORDERS ───────────────────────────

@app.route('/orders')
@login_required
def orders():
    user_orders = Order.query.filter_by(user_id=current_user.id)\
                             .order_by(Order.created_at.desc()).all()
    return render_template('orders.html', orders=user_orders)

@app.route('/orders/<int:order_id>')
@login_required
def order_detail(order_id):
    order = Order.query.get_or_404(order_id)
    if order.user_id != current_user.id and current_user.role != 'admin':
        flash('Access denied.', 'danger')
        return redirect(url_for('orders'))
    return render_template('order_detail.html', order=order)

# ─────────────────────────── RESELLER TOOLS ───────────────────────────

@app.route('/reseller')
@login_required
def reseller_dashboard():
    if current_user.role not in ('reseller', 'admin'):
        flash('Reseller access required.', 'danger')
        return redirect(url_for('index'))
    products = Product.query.filter_by(is_active=True).all()
    return render_template('reseller.html', products=products,
                           discount=current_user.reseller_discount)

@app.route('/reseller/calculate', methods=['POST'])
@login_required
def reseller_calculate():
    if current_user.role not in ('reseller', 'admin'):
        return jsonify(error='Unauthorized'), 403
    data     = request.get_json()
    items    = data.get('items', [])  # [{product_id, quantity, margin_pct}]
    results  = []
    for it in items:
        p = Product.query.get(it['product_id'])
        if not p:
            continue
        cost       = p.price * (1 - current_user.reseller_discount / 100)
        margin_pct = float(it.get('margin_pct', 20))
        sell_price = round(cost * (1 + margin_pct / 100), 2)
        profit     = round((sell_price - cost) * int(it['quantity']), 2)
        results.append(dict(
            product=p.name,
            cost_price=round(cost, 2),
            sell_price=sell_price,
            quantity=it['quantity'],
            margin_pct=margin_pct,
            profit=profit
        ))
    total_profit  = round(sum(r['profit'] for r in results), 2)
    total_revenue = round(sum(r['sell_price'] * r['quantity'] for r in results), 2)
    return jsonify(results=results, total_profit=total_profit, total_revenue=total_revenue)

# ─────────────────────────── ADMIN ───────────────────────────

@app.route('/admin')
@login_required
@admin_required
def admin_dashboard():
    total_orders    = Order.query.count()
    total_revenue   = db.session.query(db.func.sum(Order.total)).scalar() or 0
    total_products  = Product.query.count()
    total_users     = User.query.count()
    low_stock       = Product.query.filter(Product.stock < 10).all()
    recent_orders   = Order.query.order_by(Order.created_at.desc()).limit(10).all()
    return render_template('admin/dashboard.html',
                           total_orders=total_orders,
                           total_revenue=total_revenue,
                           total_products=total_products,
                           total_users=total_users,
                           low_stock=low_stock,
                           recent_orders=recent_orders)

@app.route('/admin/products')
@login_required
@admin_required
def admin_products():
    products = Product.query.order_by(Product.id.desc()).all()
    categories = Category.query.all()
    return render_template('admin/products.html', products=products, categories=categories)

@app.route('/admin/products/add', methods=['POST'])
@login_required
@admin_required
def admin_product_add():
    name    = request.form['name'].strip()
    desc    = request.form.get('description', '')
    price   = float(request.form['price'])
    stock   = int(request.form['stock'])
    cat_id  = int(request.form['category_id'])
    sku     = request.form.get('sku', '').strip() or None
    img_url = request.form.get('image_url', '')   # fallback URL field

    p = Product(name=name, description=desc, price=price, stock=stock,
                category_id=cat_id, sku=sku, image_url=img_url)
    db.session.add(p)
    db.session.flush()  # get p.id before images

    # Handle multiple uploaded image files
    files = request.files.getlist('product_images')
    saved = 0
    for i, f in enumerate(files):
        if not f or not f.filename:
            continue
        try:
            filename = save_product_image(f)
            db.session.add(ProductImage(
                product_id = p.id,
                filename   = filename,
                is_primary = (i == 0),   # first uploaded = primary
                sort_order = i
            ))
            saved += 1
        except ValueError as e:
            flash(f'Image skipped: {e}', 'warning')

    db.session.add(StockLog(product_id=p.id, change=stock, reason='Initial stock'))
    db.session.commit()
    flash(f'Product added with {saved} image(s).', 'success')
    return redirect(url_for('admin_products'))

@app.route('/admin/products/<int:pid>/edit', methods=['POST'])
@login_required
@admin_required
def admin_product_edit(pid):
    p = Product.query.get_or_404(pid)
    old_stock     = p.stock
    p.name        = request.form['name'].strip()
    p.description = request.form.get('description', '')
    p.price       = float(request.form['price'])
    p.stock       = int(request.form['stock'])
    p.category_id = int(request.form['category_id'])
    p.is_active   = 'is_active' in request.form

    # Delete images the admin checked for removal
    delete_ids = request.form.getlist('delete_image')
    for img_id in delete_ids:
        img = ProductImage.query.get(int(img_id))
        if img and img.product_id == p.id:
            delete_product_image_files(img.filename)
            db.session.delete(img)

    # Set new primary if selected
    new_primary = request.form.get('primary_image')
    if new_primary:
        for img in p.images:
            img.is_primary = (str(img.id) == new_primary)

    # Add newly uploaded images
    files  = request.files.getlist('product_images')
    saved  = 0
    start  = len(p.images)
    for i, f in enumerate(files):
        if not f or not f.filename:
            continue
        try:
            filename = save_product_image(f)
            is_first_ever = (start == 0 and i == 0)
            db.session.add(ProductImage(
                product_id = p.id,
                filename   = filename,
                is_primary = is_first_ever,
                sort_order = start + i
            ))
            saved += 1
        except ValueError as e:
            flash(f'Image skipped: {e}', 'warning')

    if p.stock != old_stock:
        db.session.add(StockLog(product_id=p.id, change=p.stock - old_stock,
                                reason='Admin edit'))
    db.session.commit()

    # If no primary set after deletions, make first image primary
    remaining = ProductImage.query.filter_by(product_id=p.id).order_by(ProductImage.sort_order).all()
    if remaining and not any(i.is_primary for i in remaining):
        remaining[0].is_primary = True
        db.session.commit()

    flash(f'Product updated. {saved} new image(s) added.', 'success')
    return redirect(url_for('admin_products'))

@app.route('/admin/products/<int:pid>/delete', methods=['POST'])
@login_required
@admin_required
def admin_product_delete(pid):
    p = Product.query.get_or_404(pid)
    p.is_active = False
    db.session.commit()
    flash('Product deactivated.', 'info')
    return redirect(url_for('admin_products'))

@app.route('/admin/products/<int:pid>/images')
@login_required
@admin_required
def get_product_images(pid):
    """Return JSON list of images for a product — used by edit modal."""
    images = ProductImage.query.filter_by(product_id=pid)\
                               .order_by(ProductImage.sort_order).all()
    result = []
    for img in images:
        result.append({
            'id':         img.id,
            'url':        url_for('static', filename=f'uploads/products/{img.filename}'),
            'thumb_url':  url_for('static', filename=f'uploads/products/thumbs/{img.filename}'),
            'is_primary': img.is_primary,
        })
    return jsonify(images=result)

@app.route('/admin/products/<int:pid>/images/set-primary/<int:img_id>', methods=['POST'])
@login_required
@admin_required
def set_primary_image(pid, img_id):
    images = ProductImage.query.filter_by(product_id=pid).all()
    for img in images:
        img.is_primary = (img.id == img_id)
    db.session.commit()
    return jsonify(success=True)

@app.route('/admin/products/<int:pid>/images/delete/<int:img_id>', methods=['POST'])
@login_required
@admin_required
def delete_image(pid, img_id):
    img = ProductImage.query.get_or_404(img_id)
    if img.product_id != pid:
        return jsonify(success=False), 403
    delete_product_image_files(img.filename)
    was_primary = img.is_primary
    db.session.delete(img)
    db.session.commit()
    # Reassign primary if needed
    if was_primary:
        remaining = ProductImage.query.filter_by(product_id=pid)\
                                      .order_by(ProductImage.sort_order).first()
        if remaining:
            remaining.is_primary = True
            db.session.commit()
    return jsonify(success=True)

# ── Bulk Upload via CSV ──

@app.route('/admin/bulk-upload', methods=['GET', 'POST'])
@login_required
@admin_required
def bulk_upload():
    if request.method == 'POST':
        file = request.files.get('csv_file')
        if not file:
            flash('No file uploaded.', 'danger')
            return redirect(url_for('bulk_upload'))
        stream   = io.StringIO(file.stream.read().decode('utf-8'))
        reader   = csv.DictReader(stream)
        added, updated, errors = 0, 0, []
        required = {'name', 'price', 'stock', 'category'}
        for i, row in enumerate(reader, start=2):
            if not required.issubset({k.lower().strip() for k in row}):
                errors.append(f'Row {i}: missing required columns')
                continue
            try:
                cat_name = row.get('category', row.get('Category', '')).strip()
                cat = Category.query.filter_by(name=cat_name).first()
                if not cat:
                    cat = Category(name=cat_name)
                    db.session.add(cat)
                    db.session.flush()
                sku = row.get('sku', row.get('SKU', '')).strip() or None
                existing = Product.query.filter_by(sku=sku).first() if sku else None
                price = float(row.get('price', row.get('Price', 0)))
                stock = int(row.get('stock', row.get('Stock', 0)))
                if existing:
                    old_stock = existing.stock
                    existing.name        = row.get('name', existing.name).strip()
                    existing.price       = price
                    existing.stock       = stock
                    existing.description = row.get('description', existing.description or '')
                    existing.image_url   = row.get('image_url', existing.image_url or '')
                    if stock != old_stock:
                        db.session.add(StockLog(product_id=existing.id,
                                                change=stock - old_stock,
                                                reason='Bulk upload'))
                    updated += 1
                else:
                    p = Product(
                        name        = row.get('name', row.get('Name', '')).strip(),
                        description = row.get('description', row.get('Description', '')),
                        price       = price,
                        stock       = stock,
                        category_id = cat.id,
                        sku         = sku,
                        image_url   = row.get('image_url', row.get('Image_URL', ''))
                    )
                    db.session.add(p)
                    db.session.flush()
                    db.session.add(StockLog(product_id=p.id, change=stock,
                                            reason='Bulk upload'))
                    added += 1
            except Exception as e:
                errors.append(f'Row {i}: {e}')
        db.session.commit()
        flash(f'Bulk upload complete: {added} added, {updated} updated, {len(errors)} errors.', 'success')
        if errors:
            for err in errors[:5]:
                flash(err, 'warning')
        return redirect(url_for('bulk_upload'))
    return render_template('admin/bulk_upload.html')

@app.route('/admin/bulk-upload/template')
@login_required
@admin_required
def bulk_template():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['name', 'description', 'price', 'stock', 'category', 'sku', 'image_url'])
    writer.writerow(['Sample Product', 'A great product', '299.00', '50', 'Electronics', 'SKU001', ''])
    writer.writerow(['Another Product', 'Another description', '499.00', '20', 'Clothing', 'SKU002', ''])
    resp = make_response(output.getvalue())
    resp.headers['Content-Disposition'] = 'attachment; filename=bulk_upload_template.csv'
    resp.headers['Content-Type'] = 'text/csv'
    return resp

# ── Admin: Orders & Users ──

@app.route('/admin/orders')
@login_required
@admin_required
def admin_orders():
    orders = Order.query.order_by(Order.created_at.desc()).all()
    return render_template('admin/orders.html', orders=orders)

@app.route('/admin/orders/<int:order_id>/status', methods=['POST'])
@login_required
@admin_required
def admin_order_status(order_id):
    order  = Order.query.get_or_404(order_id)
    new_st = request.form['status']
    VALID  = ['pending', 'confirmed', 'shipped', 'delivered', 'cancelled']
    if new_st in VALID:
        order.status = new_st
        db.session.commit()
        flash(f'Order #{order.order_ref} → {new_st}', 'success')
    return redirect(url_for('admin_orders'))

@app.route('/admin/users')
@login_required
@admin_required
def admin_users():
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template('admin/users.html', users=users)

@app.route('/admin/users/<int:uid>/role', methods=['POST'])
@login_required
@admin_required
def admin_user_role(uid):
    user = User.query.get_or_404(uid)
    user.role = request.form['role']
    user.reseller_discount = float(request.form.get('discount', 0))
    db.session.commit()
    flash(f'User {user.email} updated.', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/stock-log')
@login_required
@admin_required
def admin_stock_log():
    logs = StockLog.query.order_by(StockLog.created_at.desc()).limit(200).all()
    return render_template('admin/stock_log.html', logs=logs)

@app.route('/admin/categories', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_categories():
    if request.method == 'POST':
        name = request.form['name'].strip()
        if not Category.query.filter_by(name=name).first():
            db.session.add(Category(name=name))
            db.session.commit()
            flash('Category added.', 'success')
        else:
            flash('Category already exists.', 'warning')
        return redirect(url_for('admin_categories'))
    categories = Category.query.all()
    return render_template('admin/categories.html', categories=categories)

# ─────────────────────────── DB SEED ───────────────────────────

def seed_db():
    if User.query.count() > 0:
        return
    # Admin
    admin = User(name='Admin', email='admin@quickcart.com',
                 password_hash=bcrypt.generate_password_hash('admin123').decode(),
                 role='admin')
    # Reseller
    reseller = User(name='Reseller One', email='reseller@quickcart.com',
                    password_hash=bcrypt.generate_password_hash('reseller123').decode(),
                    role='reseller', reseller_discount=10.0)
    # Customer
    cust = User(name='Test Customer', email='customer@quickcart.com',
                password_hash=bcrypt.generate_password_hash('customer123').decode(),
                role='customer')
    db.session.add_all([admin, reseller, cust])
    # Categories
    cats = ['Electronics', 'Clothing', 'Books', 'Home & Kitchen', 'Sports']
    cat_objs = {c: Category(name=c) for c in cats}
    db.session.add_all(cat_objs.values())
    db.session.flush()
    # Products
    products = [
        ('Wireless Earbuds Pro', 'Premium noise-cancelling earbuds', 2999, 50, 'Electronics', 'https://placehold.co/300x300/1a1a2e/ffffff?text=Earbuds'),
        ('Smart Watch Series X', 'Full health tracking smartwatch', 8999, 30, 'Electronics', 'https://placehold.co/300x300/16213e/ffffff?text=SmartWatch'),
        ('USB-C Hub 7-in-1', 'Multiport adapter for laptops', 1499, 100, 'Electronics', 'https://placehold.co/300x300/0f3460/ffffff?text=USB-Hub'),
        ('Mechanical Keyboard', 'RGB backlit 100% layout', 4499, 25, 'Electronics', 'https://placehold.co/300x300/533483/ffffff?text=Keyboard'),
        ('Premium Cotton Tee', 'Soft 100% cotton round-neck', 599, 200, 'Clothing', 'https://placehold.co/300x300/e94560/ffffff?text=T-Shirt'),
        ('Slim Fit Chinos', 'Stretch chinos for all day wear', 1299, 80, 'Clothing', 'https://placehold.co/300x300/c3073f/ffffff?text=Chinos'),
        ('Python Crash Course', 'Learn Python from scratch', 799, 60, 'Books', 'https://placehold.co/300x300/6f2232/ffffff?text=Python+Book'),
        ('Clean Code', 'Robert C. Martin classic', 699, 45, 'Books', 'https://placehold.co/300x300/950740/ffffff?text=CleanCode'),
        ('Air Fryer 5L', 'Healthy cooking with less oil', 3499, 35, 'Home & Kitchen', 'https://placehold.co/300x300/44318d/ffffff?text=AirFryer'),
        ('Yoga Mat Pro', 'Non-slip eco-friendly mat', 999, 90, 'Sports', 'https://placehold.co/300x300/2b2d42/ffffff?text=YogaMat'),
        ('Resistance Bands Set', '5-level resistance set', 599, 150, 'Sports', 'https://placehold.co/300x300/8d99ae/ffffff?text=Bands'),
        ('Blender 1000W', 'Professional grade blender', 2199, 40, 'Home & Kitchen', 'https://placehold.co/300x300/ef233c/ffffff?text=Blender'),
    ]
    for name, desc, price, stock, cat, img in products:
        p = Product(name=name, description=desc, price=price, stock=stock,
                    category_id=cat_objs[cat].id, image_url=img,
                    sku=f'SKU-{name[:3].upper()}-{price}')
        db.session.add(p)
    db.session.commit()
    print('✓ Database seeded with demo data')

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        seed_db()
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
