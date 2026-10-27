from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
)
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin
from app.db.models import (
    Document,
    GeneratedFile,
    PopularSummary,
    Project,
    Role,
    Translation,
    User,
)
from app.db.session import get_db
from app.schemas.admin_management import (
    AdminDashboardResponse,
    AdminProjectListResponse,
    AdminProjectResponse,
    AdminUserListResponse,
    AdminUserResponse,
    StatusCount,
    UserStatusUpdate,
)


router = APIRouter(
    prefix="/admin",
    tags=["Admin"],
)


@router.get("/ping")
def admin_ping(
    current_admin: User = Depends(get_current_admin),
):
    return {
        "message": "Admin access granted",
        "admin_id": current_admin.id,
    }


def serialize_user(
    user: User,
    role_name: str,
) -> AdminUserResponse:
    return AdminUserResponse(
        id=user.id,
        name=user.name,
        email=user.email,
        role=role_name,
        is_active=user.is_active,
        created_at=user.created_at,
    )


def serialize_project(
    project: Project,
) -> AdminProjectResponse:
    return AdminProjectResponse(
        id=project.id,
        user_id=project.user_id,
        document_id=project.document_id,
        status=project.status,
        has_script=bool(project.script),
        has_audio=bool(project.audio_file_path),
        total_duration=project.total_duration,
        created_at=project.created_at,
    )


@router.get(
    "/users",
    response_model=AdminUserListResponse,
)
def list_users(
    search: str | None = Query(
        default=None, max_length=100
    ),
    limit: int = Query(
        default=20, ge=1, le=100
    ),
    offset: int = Query(
        default=0, ge=0
    ),
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    filters = []

    if search:
        search = search.strip()
        filters.append(
            User.email.ilike(f"%{search}%")
            | User.name.ilike(f"%{search}%")
        )

    total = db.scalar(
        select(func.count(User.id)).where(*filters)
    ) or 0

    rows = db.execute(
        select(User, Role.name)
        .join(Role, User.role_id == Role.id)
        .where(*filters)
        .order_by(User.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()

    return AdminUserListResponse(
        items=[
            serialize_user(user, role_name)
            for user, role_name in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/users/{user_id}",
    response_model=AdminUserResponse,
)
def get_user_details(
    user_id: int,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    row = db.execute(
        select(User, Role.name)
        .join(Role, User.role_id == Role.id)
        .where(User.id == user_id)
    ).first()

    if row is None:
        raise HTTPException(404, "User not found.")

    return serialize_user(row[0], row[1])


@router.patch(
    "/users/{user_id}/status",
    response_model=AdminUserResponse,
)
def change_user_status(
    user_id: int,
    payload: UserStatusUpdate,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    user = db.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
    )

    if user is None:
        raise HTTPException(404, "User not found.")

    if (
        user.id == current_admin.id
        and not payload.is_active
    ):
        raise HTTPException(
            409, "You cannot disable your own account."
        )

    role_name = db.scalar(
        select(Role.name).where(
            Role.id == user.role_id
        )
    )

    if role_name is None:
        raise HTTPException(
            409, "User role is not configured."
        )

    # Prevent disabling the final active admin.
    # Serialize this operation using the role row.
    if role_name == "admin" and not payload.is_active:
        db.scalar(
            select(Role)
            .where(Role.id == user.role_id)
            .with_for_update()
        )

        active_admins = db.scalar(
            select(func.count(User.id)).where(
                User.role_id == user.role_id,
                User.is_active.is_(True),
            )
        ) or 0

        if active_admins <= 1:
            raise HTTPException(
                409, "Cannot disable the final active admin."
            )

    user.is_active = payload.is_active
    db.commit()
    db.refresh(user)

    return serialize_user(user, role_name)


@router.get(
    "/projects",
    response_model=AdminProjectListResponse,
)
def list_projects(
    status_filter: str | None = Query(
        default=None, alias="status", max_length=30
    ),
    user_id: int | None = Query(
        default=None, gt=0
    ),
    limit: int = Query(
        default=20, ge=1, le=100
    ),
    offset: int = Query(
        default=0, ge=0
    ),
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    filters = []

    if status_filter:
        filters.append(
            Project.status == status_filter
        )

    if user_id is not None:
        filters.append(Project.user_id == user_id)

    total = db.scalar(
        select(func.count(Project.id)).where(
            *filters
        )
    ) or 0

    projects = db.scalars(
        select(Project)
        .where(*filters)
        .order_by(Project.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()

    return AdminProjectListResponse(
        items=[
            serialize_project(project)
            for project in projects
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/projects/{project_id}",
    response_model=AdminProjectResponse,
)
def get_project_details(
    project_id: int,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    project = db.get(Project, project_id)

    if project is None:
        raise HTTPException(
            404, "Project not found."
        )

    return serialize_project(project)


@router.get(
    "/analytics",
    response_model=AdminDashboardResponse,
)
def admin_analytics(
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    def count(statement):
        return db.scalar(statement) or 0

    total_users = count(
        select(func.count(User.id))
    )

    active_users = count(
        select(func.count(User.id)).where(
            User.is_active.is_(True)
        )
    )

    completed_projects = count(
        select(func.count(Project.id)).where(
            Project.status == "completed"
        )
    )

    project_rows = db.execute(
        select(
            Project.status,
            func.count(Project.id),
        )
        .group_by(Project.status)
        .order_by(Project.status)
    ).all()

    # background_jobs exists from Phase 12.
    # This table is currently accessed using raw SQL.
    job_rows = db.execute(
        text("""
            SELECT status, COUNT(*) AS total
            FROM background_jobs
            GROUP BY status
            ORDER BY status
        """)
    ).all()

    return AdminDashboardResponse(
        total_users=total_users,
        active_users=active_users,
        disabled_users=total_users - active_users,
        total_documents=count(
            select(func.count(Document.id))
        ),
        total_projects=count(
            select(func.count(Project.id))
        ),
        completed_projects=completed_projects,
        total_generated_files=count(
            select(func.count(GeneratedFile.id))
        ),
        published_summaries=count(
            select(func.count(PopularSummary.id)).where(
                PopularSummary.is_published.is_(True)
            )
        ),
        total_translations=count(
            select(func.count(Translation.id))
        ),
        total_background_jobs=sum(
            total for _, total in job_rows
        ),
        project_statuses=[
            StatusCount(status=status, count=total)
            for status, total in project_rows
        ],
        background_job_statuses=[
            StatusCount(status=status, count=total)
            for status, total in job_rows
        ],
    )
