"""Create a demo organisation populated with clearly-labelled synthetic data.

Usage: python -m scripts.seed_demo [--email demo@example.com --password '...']
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.db import session_scope
from app.core.security import hash_password
from app.models import FeeProfile, Marketplace, Organisation, ResearchProject, User
from app.services import fx, jobs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", default="demo@example.com")
    parser.add_argument("--password", default="demo-password-change-me")
    parser.add_argument("--products", type=int, default=120)
    args = parser.parse_args()
    get_settings().celery_eager = True  # run the demo jobs inline

    with session_scope() as db:
        user = db.scalar(select(User).where(func.lower(User.email) == args.email.lower()))
        if user is None:
            org = Organisation(name="Demo Organisation (synthetic data)", base_currency="GBP")
            db.add(org)
            db.flush()
            user = User(
                organisation_id=org.id,
                email=args.email.lower(),
                full_name="Demo Admin",
                password_hash=hash_password(args.password),
                role="admin",
            )
            db.add(user)
            db.flush()
        org_id = user.organisation_id
        marketplaces = dict(db.execute(select(Marketplace.code, Marketplace.id)).all())
        if not db.scalar(select(FeeProfile.id).where(FeeProfile.organisation_id == org_id)):
            # Synthetic fee schedules for the demo marketplaces only. Real marketplace fees
            # change often; enter current fees from the marketplace's published schedule.
            db.add_all(
                [
                    FeeProfile(
                        organisation_id=org_id,
                        marketplace_id=marketplaces["DEMO_UK"],
                        name="Demo UK standard",
                        referral_rate=Decimal("0.15"),
                        fixed_fee=Decimal("0.25"),
                        currency_code="GBP",
                        notes="Synthetic demo fees",
                    ),
                    FeeProfile(
                        organisation_id=org_id,
                        marketplace_id=marketplaces["DEMO_US"],
                        name="Demo US standard",
                        referral_rate=Decimal("0.15"),
                        fixed_fee=Decimal("0.30"),
                        payment_rate=Decimal("0.029"),
                        currency_code="USD",
                        notes="Synthetic demo fees",
                    ),
                ]
            )
        fx.store_rate(db, "demo-seed", "GBP", "USD", Decimal("1.27"), datetime(2026, 1, 1, tzinfo=UTC))
        project = db.scalar(select(ResearchProject).where(ResearchProject.organisation_id == org_id))
        if project is None:
            project = ResearchProject(
                organisation_id=org_id,
                name="Home fitness (demo)",
                created_by=user.id,
                description="Synthetic demo project",
            )
            db.add(project)
            db.flush()
        project_id = project.id
        db.expunge(user)

    for code, keyword in (("DEMO_UK", "yoga"), ("DEMO_US", "yoga"), ("DEMO_UK", "knife")):
        with session_scope() as db:
            u = db.get(User, user.id)
            job = jobs.create_job(
                db,
                u,
                marketplace_code=code,
                input_type="keyword",
                keyword=keyword,
                project_id=project_id,
                configuration={"result_limit": args.products},
            )
            job_id = job.id
        jobs.enqueue(job_id)
        print(f"{code} '{keyword}': job {job_id} finished")
    print(f"Demo ready. Sign in as {args.email}")


if __name__ == "__main__":
    main()
