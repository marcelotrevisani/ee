from typing import Dict

from sqlalchemy import JSON, Column, ForeignKey, Integer, String, create_engine, func
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker

from ee.config import EE_DEBUG
from ee.models import (
    AppEnvKey,
    Application,
    ApplicationEnvironment,
    EnvironmentDefinition,
)
from ee.store.gateway import EnvGateway, EnvID

# ORM Models

Base = declarative_base()


class EnvDef(Base):
    __tablename__ = "env_def"

    # short hase
    id = Column(String(8), primary_key=True)  # noqa: A003
    long_hash = Column(String(32), unique=True)
    env_def = Column(
        JSON, nullable=False
    )  # schema validation not enforced on the database layer

    app_envs = relationship("AppEnv", back_populates="env_def")


# TODO: audit trail?
class AppEnv(Base):
    __tablename__ = "app_env"

    id = Column(Integer, primary_key=True)  # noqa: A003
    app = Column(String(50))  # TODO: should have a table for apps?
    env_name = Column(String(50))
    env_def_id = Column(String(8), ForeignKey("env_def.id"))

    env_def = relationship("EnvDef", back_populates="app_envs")


# Gateway Implementation


class EnvSqliteGateway(EnvGateway):
    def __init__(self, session):
        self.session = session

    @classmethod
    def create(cls, db=""):
        if db:
            conn_str = f"sqlite:///{db}"
        else:
            conn_str = "sqlite://"  # in memory
        engine = create_engine(conn_str, echo=EE_DEBUG)
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        return cls(Session())

    @property
    def conn_str(self):
        return self.session.connection().engine.url

    def save_env_def(self, env_def: EnvironmentDefinition):
        new_env_def = EnvDef(
            id=env_def.id, long_hash=env_def.long_id, env_def=env_def.env_def
        )
        try:
            self.session.add(new_env_def)
        except Exception:
            # TODO: log?
            self.session.rollback()
        else:
            self.session.commit()

    def get_env_def(self, env_id: str) -> EnvironmentDefinition:
        try:
            env_def_orm_obj = (
                self.session.query(EnvDef).filter(EnvDef.id == env_id).one()
            )
        except NoResultFound:
            return None
        else:
            return self._env_def_from_orm_to_business_model(env_def_orm_obj)

    @classmethod
    def _env_def_from_orm_to_business_model(
        cls, env_def_orm_obj: EnvDef
    ) -> EnvironmentDefinition:
        env_def = EnvironmentDefinition.from_dict(env_def_orm_obj.env_def)
        if env_def.id != env_def_orm_obj.id:  # sanity check
            raise EnvironmentPersistenceError(
                f"IDs do not match: {env_def.id = } != {env_def_orm_obj.id = }"
            )
        return env_def

    def save_app_env(self, app_env: ApplicationEnvironment):
        app_env_orm = AppEnv(
            app=app_env.app.name, env_name=app_env.env, env_def_id=app_env.env_def.id
        )
        self.session.add(app_env_orm)
        self.session.commit()

    def get_app_env(self, app_name: str, env_name: str) -> ApplicationEnvironment:
        # we want to get the last one - so we sort by id desc and then we get the first
        query = (
            self.session.query(AppEnv)
            .filter(AppEnv.app == app_name, AppEnv.env_name == env_name)
            .order_by(AppEnv.id.desc())
        )
        if app_env_orm := query.first():
            env_def = self._env_def_from_orm_to_business_model(app_env_orm.env_def)
            app_env = ApplicationEnvironment(
                app=Application(app_env_orm.app),
                env=app_env_orm.env_name,
                env_def=env_def,
            )
            return app_env

    def list_app_envs(self) -> Dict[AppEnv, EnvID]:
        sub_query = (
            self.session.query(
                AppEnv.app, AppEnv.env_name, func.max(AppEnv.id).label("max_id")
            )
            .group_by(AppEnv.app, AppEnv.env_name)
            .subquery()
        )

        query = self.session.query(
            AppEnv.app, AppEnv.env_name, AppEnv.env_def_id
        ).filter(AppEnv.id == sub_query.c.max_id)

        return {AppEnvKey(app=r.app, env=r.env_name): r.env_def_id for r in query}


# Exceptions
class EnvironmentPersistenceError(Exception):
    pass
