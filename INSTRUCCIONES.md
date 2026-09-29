# Punto de Venta - SportElite

## Abrir sin Visual Studio (recomendado)

1. Entra a la carpeta donde está el programa (`pos_desktop`).
2. **Doble clic** en:
   - `Abrir_Punto_de_Venta.bat`  → abre el programa (puede mostrar una ventana negra de fondo)
   - o `Abrir_Punto_de_Venta.vbs` → abre **sin** ventana de terminal

### Acceso directo en el Escritorio

1. Clic derecho en `Abrir_Punto_de_Venta.vbs` → **Enviar a** → **Escritorio (crear acceso directo)**
2. (Opcional) Clic derecho en el acceso directo → **Propiedades** → puedes cambiar el icono.

Todos tus datos se guardan en esta misma carpeta:
- `pos.db` → base de datos (productos, pedidos, cotizaciones)
- `uploads/` → fotos de las prendas

**No borres ni muevas** esos archivos si quieres conservar la información.

## Primera instalación (solo una vez)

1. Instala Python 3 desde https://www.python.org/downloads/  
   (marca **Add python.exe to PATH**).
2. Abre una terminal en esta carpeta y ejecuta:

```bat
pip install -r requirements.txt
```

3. Luego usa el `.bat` o el `.vbs` para abrir siempre el programa.

## Requisitos

- customtkinter
- Pillow

```bat
pip install customtkinter Pillow
```
