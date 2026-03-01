import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from ..config import Settings


@dataclass
class Review:
    user_id: int
    username: str
    rating: int
    text: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class ReviewsState:
    reviews: List[Review] = field(default_factory=list)


def get_reviews_file(settings: Settings) -> Path:
    """Возвращает путь к файлу с отзывами"""
    reviews_dir = Path(settings.state_file).parent
    reviews_dir.mkdir(parents=True, exist_ok=True)
    return reviews_dir / "reviews.json"


def load_reviews(settings: Settings) -> ReviewsState:
    """Загружает отзывы из JSON файла"""
    reviews_file = get_reviews_file(settings)
    
    if not reviews_file.exists():
        return ReviewsState()
    
    try:
        with open(reviews_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        reviews = []
        for r in data.get("reviews", []):
            reviews.append(Review(
                user_id=r["user_id"],
                username=r["username"],
                rating=r["rating"],
                text=r.get("text"),
                created_at=r.get("created_at", datetime.now(timezone.utc).isoformat())
            ))
        
        return ReviewsState(reviews=reviews)
    except Exception:
        return ReviewsState()


def save_review(review: Review, settings: Settings) -> None:
    """Сохраняет новый отзыв"""
    state = load_reviews(settings)
    state.reviews.append(review)
    save_reviews(state, settings)


def save_reviews(state: ReviewsState, settings: Settings) -> None:
    """Сохраняет все отзывы в JSON файл"""
    reviews_file = get_reviews_file(settings)
    
    data = {
        "reviews": [asdict(r) for r in state.reviews]
    }
    
    with open(reviews_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_reviews_list(settings: Settings, limit: int = 20) -> List[Review]:
    """Возвращает список отзывов (последние сначала)"""
    state = load_reviews(settings)
    return list(reversed(state.reviews[-limit:]))

def delete_review(review_id: int, settings: Settings) -> bool:
    """Удаляет отзыв по индексу в списке (для пользователя - только свой)"""
    state = load_reviews(settings)
    if 0 <= review_id < len(state.reviews):
        state.reviews.pop(review_id)
        save_reviews(state, settings)
        return True
    return False


def delete_review_by_user_id(user_id: int, review_index: int, settings: Settings) -> bool:
    """Удаляет отзыв пользователя по индексу (только если это отзыв этого пользователя)"""
    state = load_reviews(settings)
    user_reviews = [i for i, r in enumerate(state.reviews) if r.user_id == user_id]
    if 0 <= review_index < len(user_reviews):
        actual_index = user_reviews[review_index]
        state.reviews.pop(actual_index)
        save_reviews(state, settings)
        return True
    return False

