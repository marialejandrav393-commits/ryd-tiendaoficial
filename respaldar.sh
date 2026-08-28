#!/bin/bash
FECHA=$(date +%Y-%m-%d_%H-%M-%S)
DESTINO=~/sistema_inventario/backups

mkdir -p $DESTINO
cp ~/sistema_inventario/base_datos.db $DESTINO/base_datos_$FECHA.db
echo "Respaldo creado exitosamente en: $DESTINO/base_datos_$FECHA.db"
