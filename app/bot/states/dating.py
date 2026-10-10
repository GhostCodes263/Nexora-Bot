from aiogram.fsm.state import State, StatesGroup


class DSetup(StatesGroup):
    answering = State()
