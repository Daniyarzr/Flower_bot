import os
import aiofiles
import uuid
from fastapi import FastAPI, Request, Form, Depends, HTTPException, status, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import select, update, delete, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.db import get_sessionmaker, init_engine
from app.models import User, Product, Request as Order, UserRole, CategoryEnum, RequestStatus, BotText
from app.config import load_config
from aiogram import Bot

app = FastAPI(title="BLOOM lavka Admin")
config = load_config()
init_engine(config.db_url)

# Создаем папку для загрузок, если её нет
os.makedirs("static/uploads", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Настройка сессий
app.add_middleware(
    SessionMiddleware, 
    secret_key="FIXED_SECRET_KEY_123",
    session_cookie="flower_session",
    same_site="lax",
    https_only=False
)

templates = Jinja2Templates(directory="app/templates/admin")

# Зависимость для БД
async def get_db():
    async_session = get_sessionmaker()
    async with async_session() as session:
        yield session

# --- МАРШРУТЫ АВТОРИЗАЦИИ ---

@app.get("/", response_class=HTMLResponse)
async def login_page(request: Request):
    if request.session.get("is_logged_in"):
        return RedirectResponse("/dashboard")
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
async def login(request: Request, password: str = Form(...)):
    # Пароль из .env или дефолтный 'admin'
    correct_password = os.getenv("ADMIN_PASSWORD", "admin").strip()
    if password.strip() == correct_password:
        request.session["is_logged_in"] = True
        return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse("/?error=1", status_code=status.HTTP_303_SEE_OTHER)

@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/")

# --- ГЛАВНАЯ (DASHBOARD) ---

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, session: AsyncSession = Depends(get_db)):
    if not request.session.get("is_logged_in"): 
        return RedirectResponse("/")
    
    # Сбор статистики
    stats = {
        "new": await session.scalar(select(func.count(Order.id)).where(Order.status == RequestStatus.NEW)) or 0,
        "in_work": await session.scalar(select(func.count(Order.id)).where(Order.status == RequestStatus.IN_WORK)) or 0,
        "done": await session.scalar(select(func.count(Order.id)).where(Order.status == RequestStatus.DONE)) or 0,
        "total_orders": await session.scalar(select(func.count(Order.id))) or 0,
        "products": await session.scalar(select(func.count(Product.id))) or 0,
        "users": await session.scalar(select(func.count(User.id))) or 0,
    }

    bot_texts = await session.execute(select(BotText))
    bot_texts_dict = {bt.key: bt.value for bt in bot_texts.scalars().all()}

    return templates.TemplateResponse(
        "dashboard.html", 
        {"request": request, "stats": stats, "bot_texts": bot_texts_dict}
    )

# --- КАТАЛОГ ---

@app.get("/catalog", response_class=HTMLResponse)
async def catalog(request: Request, session: AsyncSession = Depends(get_db)):
    if not request.session.get("is_logged_in"): return RedirectResponse("/")
    products = (await session.execute(select(Product).order_by(Product.id.desc()))).scalars().all()
    return templates.TemplateResponse("catalog.html", {"request": request, "products": products})

@app.post("/catalog/add")
async def add_product(
    request: Request, 
    title: str = Form(...), 
    price: int = Form(...), 
    description: str = Form(None),
    category: str = Form(...),
    file: UploadFile = File(None),
    session: AsyncSession = Depends(get_db)
):
    if not request.session.get("is_logged_in"): return RedirectResponse("/")
    
    image_url = None
    if file:
        filename = f"{uuid.uuid4()}.{file.filename.split('.')[-1]}"
        filepath = os.path.join("static/uploads", filename)
        async with aiofiles.open(filepath, "wb") as f:
            await f.write(await file.read())
        image_url = f"/static/uploads/{filename}"
    
    product = Product(
        title=title,
        price=price,
        description=description,
        category=CategoryEnum(category),
        image_url=image_url
    )
    session.add(product)
    await session.commit()
    return RedirectResponse("/catalog", status_code=status.HTTP_303_SEE_OTHER)

# Новый роут: GET для формы редактирования
@app.get("/catalog/edit/{product_id}", response_class=HTMLResponse)
async def edit_product_form(request: Request, product_id: int, session: AsyncSession = Depends(get_db)):
    if not request.session.get("is_logged_in"): return RedirectResponse("/")
    
    product = await session.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    return templates.TemplateResponse("edit_product.html", {"request": request, "product": product})

# Новый роут: POST для сохранения изменений
@app.post("/catalog/edit/{product_id}")
async def edit_product(
    request: Request, 
    product_id: int,
    title: str = Form(...), 
    price: int = Form(...), 
    description: str = Form(None),
    category: str = Form(...),
    file: UploadFile = File(None),
    session: AsyncSession = Depends(get_db)
):
    if not request.session.get("is_logged_in"): return RedirectResponse("/")
    
    product = await session.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    product.title = title
    product.price = price
    product.description = description
    product.category = CategoryEnum(category)
    
    if file:
        # Удаляем старую фото, если была
        if product.image_url and os.path.exists(product.image_url.lstrip('/')):
            os.remove(product.image_url.lstrip('/'))
        
        filename = f"{uuid.uuid4()}.{file.filename.split('.')[-1]}"
        filepath = os.path.join("static/uploads", filename)
        async with aiofiles.open(filepath, "wb") as f:
            await f.write(await file.read())
        product.image_url = f"/static/uploads/{filename}"
    
    await session.commit()
    return RedirectResponse("/catalog", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/catalog/delete/{product_id}")
async def delete_product(request: Request, product_id: int, session: AsyncSession = Depends(get_db)):
    if not request.session.get("is_logged_in"): return RedirectResponse("/")
    
    product = await session.get(Product, product_id)
    if product and product.image_url and os.path.exists(product.image_url.lstrip('/')):
        os.remove(product.image_url.lstrip('/'))
    
    await session.execute(delete(Product).where(Product.id == product_id))
    await session.commit()
    return RedirectResponse("/catalog", status_code=status.HTTP_303_SEE_OTHER)

# --- ЗАЯВКИ ---

@app.get("/orders", response_class=HTMLResponse)
async def orders_page(request: Request, session: AsyncSession = Depends(get_db)):
    if not request.session.get("is_logged_in"): return RedirectResponse("/")
    result = await session.execute(
        select(Order)
        .options(joinedload(Order.user), joinedload(Order.product))  # Добавьте joinedload для product
        .order_by(Order.id.desc()).limit(50)
    )
    orders = result.scalars().all()
    return templates.TemplateResponse("orders.html", {"request": request, "orders": orders})

@app.post("/orders/status")
async def change_order_status(
    request: Request,
    order_id: int = Form(...), 
    status_val: str = Form(...), 
    session: AsyncSession = Depends(get_db)
):
    if not request.session.get("is_logged_in"): return RedirectResponse("/")
    await session.execute(update(Order).where(Order.id == order_id).values(status=RequestStatus(status_val)))
    await session.commit()
    return RedirectResponse("/orders", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/orders/delete/{order_id}")
async def delete_order(request: Request, order_id: int, session: AsyncSession = Depends(get_db)):
    if not request.session.get("is_logged_in"): return RedirectResponse("/")
    await session.execute(delete(Order).where(Order.id == order_id))
    await session.commit()
    return RedirectResponse("/orders", status_code=status.HTTP_303_SEE_OTHER)

# --- ПОЛЬЗОВАТЕЛИ ---

@app.get("/users", response_class=HTMLResponse)
async def users_page(request: Request, session: AsyncSession = Depends(get_db)):
    if not request.session.get("is_logged_in"): return RedirectResponse("/")
    users = (await session.execute(select(User).order_by(User.id.desc()))).scalars().all()
    return templates.TemplateResponse("users.html", {"request": request, "users": users})

@app.post("/users/set_role")
async def set_user_role(
    request: Request,
    user_id: int = Form(...), 
    new_role: str = Form(...), 
    session: AsyncSession = Depends(get_db)
):
    if not request.session.get("is_logged_in"): return RedirectResponse("/")
    await session.execute(update(User).where(User.id == user_id).values(role=UserRole(new_role)))
    await session.commit()
    return RedirectResponse("/users", status_code=status.HTTP_303_SEE_OTHER)



# Создаем объект бота внутри web_admin
bot_app = Bot(token=config.bot_token)

@app.post("/broadcast")
async def broadcast_message(
    request: Request, 
    message: str = Form(...), 
    session: AsyncSession = Depends(get_db)
):
    if not request.session.get("is_logged_in"): 
        return RedirectResponse("/")

    # 1. Получаем ID всех пользователей из базы
    result = await session.execute(select(User.tg_id))
    user_ids = result.scalars().all()

    count = 0
    errors = 0

    # 2. Рассылаем
    for user_id in user_ids:
        try:
            await bot_app.send_message(user_id, message)
            count += 1
        except Exception as e:
            print(f"Ошибка отправки пользователю {user_id}: {e}")
            errors += 1
    
    # 3. Возвращаемся на главную с уведомлением (через query-параметр)
    return RedirectResponse(f"/dashboard?sent={count}&errors={errors}", status_code=status.HTTP_303_SEE_OTHER)