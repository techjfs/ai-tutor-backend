from pydantic import BaseModel

class UserQuestion(BaseModel):
    question: str