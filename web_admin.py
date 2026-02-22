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
from PIL import Image  
import io
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

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

async def save_optimized_image(file: UploadFile) -> str:
    """Конвертирует в WebP, меняет размер и сохраняет."""
    content = await file.read()
    img = Image.open(io.BytesIO(content))

    # Конвертируем в RGB (для поддержки PNG и GIF)
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    # Уменьшаем до 1024px по большей стороне (сохраняя пропорции)
    img.thumbnail((1024, 1024))

    filename = f"{uuid.uuid4()}.webp"
    filepath = os.path.join("static/uploads", filename)

    # Сохраняем с качеством 80%
    img.save(filepath, format="WEBP", quality=80, optimize=True)
    
    return f"/static/uploads/{filename}"

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
    if file and file.filename:
        image_url = await save_optimized_image(file)
    
    product = Product(
        title=title, price=price, description=description,
        category=CategoryEnum(category), image_url=image_url
    )
    session.add(product)
    await session.commit()
    return RedirectResponse("/catalog", status_code=303)

# Новый роут: GET для формы редактирования
# 1. Эта функция исправляет ошибку 405 (открывает страницу редактирования)
@app.get("/catalog/edit/{product_id}", response_class=HTMLResponse)
async def edit_product_page(request: Request, product_id: int, session: AsyncSession = Depends(get_db)):
    if not request.session.get("is_logged_in"): return RedirectResponse("/")
    
    product = await session.get(Product, product_id)
    if not product: raise HTTPException(status_code=404)
    
    # Убедитесь, что у вас есть файл edit_product.html в папке шаблонов
    return templates.TemplateResponse("edit_product.html", {"request": request, "product": product})

# 2. Эта функция сохраняет изменения и делает фото в формате WebP
@app.post("/catalog/edit/{product_id}")
async def edit_product_save(
    request: Request, 
    product_id: int,
    title: str = Form(...), 
    price: int = Form(...), 
    description: str = Form(None),
    category: str = Form(...),
    file: UploadFile = File(None), # Здесь стоит None по умолчанию
    session: AsyncSession = Depends(get_db)
):
    if not request.session.get("is_logged_in"): return RedirectResponse("/")
    
    product = await session.get(Product, product_id)
    if not product: raise HTTPException(status_code=404)
    
    # Обновляем текстовые поля (они обновятся в любом случае)
    product.title = title
    product.price = price
    product.description = description
    product.category = CategoryEnum(category)
    
    # КЛЮЧЕВОЕ ИЗМЕНЕНИЕ: проверяем, загружен ли новый файл
    # Если файл не выбран, браузер пришлет пустой объект UploadFile с пустым именем
    if file and file.filename:
        # 1. Удаляем старое физически с диска (если оно было)
        if product.image_url:
            old_path = product.image_url.lstrip('/')
            if os.path.exists(old_path):
                os.remove(old_path)
        
        # 2. Сохраняем новое фото в WebP
        product.image_url = await save_optimized_image(file)
    
    # Если файл не прислали (file.filename пустой), 
    # product.image_url останется прежним, каким и был в базе.

    await session.commit()
    return RedirectResponse("/catalog", status_code=303)

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

@app.post("/bot-texts/save")
async def save_bot_texts(
    request: Request,
    start_message: str = Form(...),
    support_message: str = Form(...),
    session: AsyncSession = Depends(get_db)
):
    if not request.session.get("is_logged_in"):
        return RedirectResponse("/")

    # Словарь соответствия полей из формы и ключей в базе данных
    texts_to_save = {
        "start_message": start_message,
        "support_message": support_message
    }

    for key, value in texts_to_save.items():
        # Ищем существующую запись по ключу
        result = await session.execute(select(BotText).where(BotText.key == key))
        bot_text = result.scalar_one_or_none()

        if bot_text:
            bot_text.value = value
        else:
            # Если записи еще нет, создаем новую
            bot_text = BotText(key=key, value=value)
            session.add(bot_text)

    await session.commit()
    return RedirectResponse("/dashboard", status_code=303)

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

    # 1. Получаем ID всех пользователей
    result = await session.execute(select(User.tg_id))
    user_ids = result.scalars().all()

    # 2. Получаем имя бота, чтобы создать ссылку на него
    bot_info = await bot_app.get_me()
    bot_link = f"https://t.me/{bot_info.username}?start=ml" # Добавили параметр, чтобы кнопка всегда была активна

    # 3. Создаем клавиатуру с кнопкой возврата в меню
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="💐 Перейти в главное меню", 
                url=bot_link
            )
        ]
    ])

    count = 0
    errors = 0

    # 4. Рассылаем
    for user_id in user_ids:
        try:
            await bot_app.send_message(
                chat_id=user_id,
                text=message,
                reply_markup=keyboard, # Кнопка под сообщением
                parse_mode="HTML"
            )
            count += 1
        except Exception as e:
            print(f"Ошибка рассылки {user_id}: {e}")
            errors += 1

    return RedirectResponse(f"/dashboard?sent={count}&errors={errors}", status_code=303)