# Instrucciones rápidas - SportElite POS

## Abrir el programa

1. Doble clic en `Abrir_Punto_de_Venta.bat`  
   o en `Abrir_Punto_de_Venta.vbs` (sin ventana negra).

## Primera vez

1. Python 3.12 instalado (Add to PATH).
2. Ejecutar `INSTALAR_LIBRERIAS.bat`.
3. Tener `pos.db` y la carpeta `uploads/` con las fotos en la misma carpeta que `main.py`.

## Datos

- `pos.db` → productos, pedidos, cotizaciones.
- `uploads/` → imágenes (no borrar ni renombrar archivos si quieres que coincidan con la base).

## Exportar / importar

Desde **Productos** en el programa:

- Catalogo PDF (completo o por categoría/filtro).
- Datos JSON (backup de productos).
- Fotos ZIP (backup de imágenes originales).

## Problemas

- Ejecuta `Diagnosticar.bat`.
- Si cierra con error -1073741819, usa Python 3.12.
- Revisa `error_inicio.log` si se crea al fallar el arranque.
