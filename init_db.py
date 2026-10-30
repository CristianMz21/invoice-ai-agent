"""Script de inicialización y migración limpia del esquema relacional 3NF.

Permite resetear la base de datos eliminando tablas previas y desplegando
la arquitectura normalizada (Empresa, Tercero, Factura, ItemFactura)
con soporte Multi-Tenancy y modelo dual (Cuentas por Pagar / Cobrar).
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from db_handler import _motor, obtener_o_crear_empresa_demo
from logging_config import configurar_logging
from models import Base

logger = logging.getLogger("init_db")


def migrar_base_de_datos() -> None:
    """Elimina las tablas obsoletas y recrea el esquema 3NF multi-tenancy."""
    configurar_logging()
    logger.info("Iniciando migración relacional 3NF de la base de datos...")

    # Drop de tablas existentes para garantizar un esquema limpio
    Base.metadata.drop_all(_motor)
    logger.info("Tablas previas eliminadas exitosamente.")

    # Creación del nuevo esquema normalizado
    Base.metadata.create_all(_motor)
    logger.info("Nuevo esquema 3NF creado (empresas, terceros, facturas, items_factura).")

    # Semillado de la empresa base (Tenant demo)
    with Session(_motor, expire_on_commit=False) as sesion, sesion.begin():
        empresa = obtener_o_crear_empresa_demo(sesion)
        logger.info("Tenant inicial activo: '%s' (NIT: %s)", empresa.razon_social, empresa.nit)

    logger.info("Migración relacional finalizada con éxito.")


if __name__ == "__main__":
    migrar_base_de_datos()
