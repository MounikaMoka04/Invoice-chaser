from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Client, User
from ..schemas import ClientCreate, ClientOut, ClientUpdate
from ..security import get_current_user

router = APIRouter(prefix="/clients", tags=["clients"])


def get_owned_client(db: Session, user: User, client_id: int) -> Client:
    client = db.get(Client, client_id)
    if not client or client.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Client not found")
    return client


@router.get("", response_model=list[ClientOut])
def list_clients(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(Client).filter(Client.user_id == user.id).order_by(Client.name).all()


@router.post("", response_model=ClientOut, status_code=status.HTTP_201_CREATED)
def create_client(data: ClientCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    client = Client(user_id=user.id, **data.model_dump())
    db.add(client)
    db.commit()
    return client


@router.patch("/{client_id}", response_model=ClientOut)
def update_client(client_id: int, data: ClientUpdate, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    client = get_owned_client(db, user, client_id)
    for field, value in data.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(client, field, value)
    db.commit()
    return client


@router.delete("/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_client(client_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Deletes the client and, with them, every invoice and message (data removal request)."""
    db.delete(get_owned_client(db, user, client_id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
