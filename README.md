# Punto de Venta - SportElite

Aplicación de escritorio (Windows) para catálogo, ventas, cotizaciones y pedidos.

## Contenido del repositorio

| Archivo | Uso |
|---------|-----|
| `main.py` | Programa principal |
| `pos.db` | Base de datos (productos, stock, pedidos…) |
| `uploads/` | Carpeta de fotos de productos (vacía en GitHub) |
| `Abrir_Punto_de_Venta.bat` | Abrir el POS |
| `Abrir_Punto_de_Venta.vbs` | Abrir sin ventana de consola |
| `INSTALAR_LIBRERIAS.bat` | Instalar dependencias |
| `Diagnosticar.bat` | Comprobar Python y librerías |
| `requirements.txt` | Dependencias pip |

## Importante sobre las fotos

Las **~1000 fotografías no van en GitHub** (pesan demasiado).  
Debes copiar tu carpeta `uploads/` local junto a `main.py` y `pos.db`.

Estructura en tu PC:

```
SportElite_POS/
  main.py
  pos.db
  uploads/          ← aquí tus fotos (nombres como en la base de datos)
  Abrir_Punto_de_Venta.bat
  ...
```

## Instalación (una vez)

1. Instala **Python 3.12** desde https://www.python.org/downloads/  
   (marca *Add python.exe to PATH*).
2. En esta carpeta, ejecuta `INSTALAR_LIBRERIAS.bat`  
   o: `pip install -r requirements.txt`
3. Copia tus fotos a `uploads/` si aún no están.
4. Abre con `Abrir_Punto_de_Venta.bat` o el `.vbs`.

## Uso en GitHub

1. Crea un repositorio nuevo.
2. Sube **estos archivos de código** (no subas `uploads/` con miles de MB).
3. En cada PC, clona el repo y copia `uploads/` + si hace falta un `pos.db` actualizado.

## Exportar / importar (desde el programa)

En **Productos**:

- **Datos JSON** → exportar o importar productos (stock, categorías, filtros).
- **Fotos ZIP** → empaquetar o restaurar fotos originales.
- **Exportar Catalogo** → PDF completo o por categoría/filtro.

Así puedes mover datos entre equipos sin subir el catálogo de fotos a GitHub.

## Requisitos

- Windows
- Python 3.11 o 3.12 (recomendado)
- `customtkinter`, `Pillow`
