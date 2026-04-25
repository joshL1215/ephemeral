from pydantic import BaseModel
from .errors import UnknownProfileError


class ImageProfile(BaseModel):
    name: str
    image: str
    description: str
    packages: list[str]
    size_mb: int
    typical_use_cases: list[str]
    user: str = "root"
    keepalive_command: list[str] = ["sleep", "infinity"]


CATALOG: list[ImageProfile] = [
    ImageProfile(
        name="python-base",
        image="python:3.11-slim",
        description="Minimal Python 3.11. No data libraries pre-installed.",
        packages=["python3.11", "pip"],
        size_mb=120,
        typical_use_cases=[
            "simple scripts",
            "string manipulation",
            "API calls without heavy data processing",
        ],
    ),
    ImageProfile(
        name="python-data",
        image="quay.io/jupyter/scipy-notebook",
        description="Python with full scientific stack pre-installed.",
        packages=[
            "pandas",
            "numpy",
            "matplotlib",
            "scipy",
            "scikit-learn",
            "seaborn",
            "statsmodels",
            "requests",
            "beautifulsoup4",
        ],
        size_mb=3200,
        typical_use_cases=[
            "data analysis with pandas",
            "numerical computing",
            "plotting and visualization",
            "ML with scikit-learn",
            "CSV/Excel/Parquet processing",
        ],
    ),
]

_catalog_by_name: dict[str, ImageProfile] = {p.name: p for p in CATALOG}


def get_profile(name: str) -> ImageProfile:
    try:
        return _catalog_by_name[name]
    except KeyError:
        raise UnknownProfileError(f"Unknown profile: {name!r}")
