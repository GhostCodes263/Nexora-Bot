from aiogram.fsm.state import State, StatesGroup


class Apply(StatesGroup):
    answering = State()   # applicant answering questions
    more_info = State()   # applicant answering a reviewer's request


class ReviewNote(StatesGroup):
    note = State()        # reviewer typing the reason for reject / needs-info
