from aiogram.fsm.state import State, StatesGroup


class RequestFSM(StatesGroup):
    need_datetime = State()
    delivery_type = State()
    address = State()
    payment_type = State()
    customer_name = State()
    phone = State()
    comment = State()
    confirm = State()


class AddProductFSM(StatesGroup):
    category = State()
    title = State()
    price = State()
    photo = State()
    confirm = State()

from aiogram.fsm.state import StatesGroup, State

class SupportFSM(StatesGroup):
    text = State()
