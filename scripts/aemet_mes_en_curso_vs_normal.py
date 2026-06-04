#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AEMET OpenData · Panel diario: mes en curso vs normal climática.

Pensado para automatizar en GitHub Actions y publicar en GitHub Pages/Blogger.

Genera para varias estaciones de Valencia:
  - Temperatura media mensual en curso vs normal.
  - Temperatura máxima media mensual en curso vs normal.
  - Humedad relativa media mensual en curso vs normal.

Salida:
  docs/clima/index.html
  docs/clima/graficos/*.png
  docs/clima/datos/resumen.csv

Uso manual en PowerShell:
  $env:AEMET_API_KEY="TU_API_KEY_DE_AEMET"
  pip install requests pandas matplotlib
  python aemet_mes_en_curso_vs_normal.py

Uso con fecha concreta:
  python aemet_mes_en_curso_vs_normal.py --fecha 2026-06-15

Uso con menos estaciones, por ejemplo solo València y Xàtiva:
  python aemet_mes_en_curso_vs_normal.py --estaciones 8416,8293X

Notas:
  - AEMET OpenData devuelve primero una URL temporal en el campo "datos".
    Después hay que hacer una segunda descarga sobre esa URL.
  - Las climatologías diarias pueden publicarse con retraso. El script calcula
    el valor del mes en curso con los días que AEMET devuelva.
  - Si una estación no tiene normal climática de humedad relativa, no se para
    todo el proceso: lo deja marcado como "sin datos" y continúa.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import pandas as pd
import requests

BASE_API = "https://opendata.aemet.es/opendata/api"

# Estaciones extraídas del HTML que pasaste.
ESTACIONES_VALENCIA: list[tuple[str, str]] = [
    ("8381X", "Ademuz"),
    ("8072Y", "Barx"),
    ("8270X", "Bicorp"),
    ("8300X", "Carcaixent"),
    ("8395X", "Chelva"),
    ("8005X", "Fontanars dels Alforins"),
    ("8193E", "Jalance"),
    ("8409X", "Llíria"),
    ("8058Y", "Miramar"),
    ("8058X", "Oliva"),
    ("8283X", "Ontinyent"),
    ("8325X", "Polinyà de Xúquer"),
    ("8446Y", "Sagunt/Sagunto"),
    ("8328X", "Sollana"),
    ("8337X", "Turís"),
    ("8309X", "Utiel"),
    ("8414A", "Valencia Aeropuerto"),
    ("8416", "València"),
    ("8293X", "Xàtiva"),
    ("8203O", "Zarra"),
]

MESES = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril", 5: "Mayo", 6: "Junio",
    7: "Julio", 8: "Agosto", 9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
}

MES_TEXTO = {
    "ene": 1, "enero": 1,
    "feb": 2, "febrero": 2,
    "mar": 3, "marzo": 3,
    "abr": 4, "abril": 4,
    "may": 5, "mayo": 5,
    "jun": 6, "junio": 6,
    "jul": 7, "julio": 7,
    "ago": 8, "agosto": 8,
    "sep": 9, "sept": 9, "septiembre": 9,
    "oct": 10, "octubre": 10,
    "nov": 11, "noviembre": 11,
    "dic": 12, "diciembre": 12,
}

VARIABLES = {
    "tmed": {
        "titulo": "Temperatura media",
        "unidad": "°C",
        "calculo": "media",
        "diario": [
            "tmed",
            "temperatura_media",
            "temp_media",
            "temperatura media",
        ],
        "normal": [
            # Nombre habitual en normales climáticas AEMET
            "tm_mes_md",

            # Alternativas defensivas
            "tmed",
            "temperatura_media",
            "temp_media",
            "temperatura media",
            "temperatura_media_mensual",
            "media_temperatura_media",
        ],
    },

    "tmax": {
        "titulo": "Temperatura máxima media",
        "unidad": "°C",
        "calculo": "media",
        "diario": [
            "tmax",
            "temperatura_maxima",
            "temperatura_máxima",
            "temp_max",
            "maxima",
            "máxima",
        ],
        "normal": [
            # Nombre habitual en normales climáticas AEMET
            "tm_max_md",

            # Alternativas defensivas
            "tmax",
            "temperatura_maxima_media",
            "temperatura_máxima_media",
            "media_temperaturas_maximas",
            "media_temperaturas_máximas",
            "temperatura_maxima",
            "temperatura_máxima",
            "temp_max",
            "maxima_media",
            "máxima_media",
        ],
    },

    "hr": {
        "titulo": "Humedad relativa media",
        "unidad": "%",
        "calculo": "media",
        "diario": [
            "hrmedia",
            "hr_media",
            "hrmed",
            "hr_med",
            "hr",
            "humedad_relativa_media",
            "humedad media",
            "humedad_media",
            "humedad_relativa",
            "humedad",
        ],
        "normal": [
            # Nombre habitual en normales climáticas AEMET
            "hr_md",

            # Alternativas defensivas
            "hrmedia",
            "hr_media",
            "hrmed",
            "hr_med",
            "hr",
            "humedad_relativa_media",
            "humedad media",
            "humedad_media",
            "humedad_relativa",
            "humedad",
        ],
    },
}

class AemetError(RuntimeError):
    pass


@dataclass
class ResultadoVariable:
    estacion: str
    nombre: str
    variable: str
    titulo: str
    unidad: str
    estado: str
    mensaje: str
    normal: float | None = None
    observado: float | None = None
    diferencia: float | None = None
    dias: int | None = None
    ultimo_dia: str | None = None
    grafico: str | None = None


def quitar_acentos_basico(texto: str) -> str:
    return (
        texto.replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u")
        .replace("Á", "a").replace("É", "e").replace("Í", "i").replace("Ó", "o").replace("Ú", "u")
        .replace("à", "a").replace("è", "e").replace("ì", "i").replace("ò", "o").replace("ù", "u")
        .replace("À", "a").replace("È", "e").replace("Ì", "i").replace("Ò", "o").replace("Ù", "u")
        .replace("ï", "i").replace("ü", "u").replace("ñ", "n")
    )


def normalizar_nombre_columna(col: Any) -> str:
    texto = quitar_acentos_basico(str(col).strip().lower())
    texto = re.sub(r"[^a-z0-9]+", "_", texto)
    return texto.strip("_")


def normalizar_columnas(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [normalizar_nombre_columna(c) for c in df.columns]
    return df


def buscar_columna(df: pd.DataFrame, candidatos: list[str]) -> str | None:
    columnas = set(df.columns)
    for cand in candidatos:
        c = normalizar_nombre_columna(cand)
        if c in columnas:
            return c
    return None


def a_float(valor: Any) -> float:
    """Convierte valores tipo '12,3', 'Ip', '0,0', '23.1(14)' a float."""
    if valor is None or pd.isna(valor):
        return float("nan")

    texto = str(valor).strip()
    if not texto:
        return float("nan")

    if texto.lower() in {"ip", "tr", "traza"}:
        return 0.0

    texto = texto.replace(",", ".")
    match = re.search(r"-?\d+(?:\.\d+)?", texto)
    if not match:
        return float("nan")
    return float(match.group(0))


def leer_json(resp: requests.Response) -> Any:
    ultimo_error: Exception | None = None
    for enc in (resp.encoding, "utf-8-sig", "utf-8", "latin-1"):
        if not enc:
            continue
        try:
            return json.loads(resp.content.decode(enc))
        except Exception as exc:  # noqa: BLE001
            ultimo_error = exc
    raise AemetError(f"No se pudo leer JSON desde {resp.url}: {ultimo_error}")


def api_key_desde_entorno(api_key_arg: str | None) -> str:
    api_key = (api_key_arg or os.getenv("AEMET_API_KEY") or "").strip()
    if not api_key:
        raise AemetError(
            "Falta la API key. En PowerShell puedes poner:\n"
            "$env:AEMET_API_KEY=\"TU_API_KEY_DE_AEMET\""
        )
    return api_key


def descargar_endpoint(endpoint: str, api_key: str) -> Any:
    """Descarga endpoint AEMET en dos pasos: descriptor -> URL de datos."""
    url = f"{BASE_API}{endpoint}"
    headers = {
        "api_key": api_key,
        "accept": "application/json",
        "cache-control": "no-cache",
    }

    resp = requests.get(url, headers=headers, timeout=60)

    # Compatibilidad con ejemplos antiguos donde la API key se pasa como parámetro.
    if resp.status_code in {401, 403}:
        resp = requests.get(url, params={"api_key": api_key}, timeout=60)

    if resp.status_code == 429:
        raise AemetError("AEMET devuelve 429: demasiadas peticiones. Espera un poco y vuelve a ejecutar.")

    try:
        resp.raise_for_status()
    except requests.HTTPError as exc:
        raise AemetError(f"Error en la primera llamada AEMET: {resp.status_code} {resp.text[:500]}") from exc

    descriptor = leer_json(resp)
    if not isinstance(descriptor, dict) or "datos" not in descriptor:
        raise AemetError(f"Respuesta inesperada de AEMET: {descriptor}")

    datos_url = descriptor["datos"]
    datos_resp = requests.get(datos_url, timeout=60)
    try:
        datos_resp.raise_for_status()
    except requests.HTTPError as exc:
        raise AemetError(f"Error descargando la URL de datos: {datos_resp.status_code} {datos_resp.text[:500]}") from exc

    return leer_json(datos_resp)


def descargar_diarios_mes(api_key: str, estacion: str, fecha_ref: date) -> pd.DataFrame:
    inicio = fecha_ref.replace(day=1)
    fecha_ini = f"{inicio.isoformat()}T00:00:00UTC"
    fecha_fin = f"{fecha_ref.isoformat()}T23:59:59UTC"

    endpoint = (
        "/valores/climatologicos/diarios/datos/"
        f"fechaini/{fecha_ini}/"
        f"fechafin/{fecha_fin}/"
        f"estacion/{estacion}"
    )
    data = descargar_endpoint(endpoint, api_key)
    df = normalizar_columnas(pd.DataFrame(data))
    return df


def descargar_normales(api_key: str, estacion: str) -> pd.DataFrame:
    endpoint = f"/valores/climatologicos/normales/estacion/{estacion}"
    data = descargar_endpoint(endpoint, api_key)
    df = normalizar_columnas(pd.DataFrame(data))
    return df


def extraer_mes(valor: Any) -> int | None:
    if valor is None or pd.isna(valor):
        return None

    texto = quitar_acentos_basico(str(valor).strip().lower())
    if not texto or texto in {"anual", "ano", "año", "year"}:
        return None

    if texto.isdigit():
        mes = int(texto)
        return mes if 1 <= mes <= 12 else None

    if texto in MES_TEXTO:
        return MES_TEXTO[texto]
    if texto[:3] in MES_TEXTO:
        return MES_TEXTO[texto[:3]]

    # Fechas tipo 2026-06-01 o 2026/06
    match = re.search(r"\d{4}[-/](\d{1,2})", texto)
    if match:
        mes = int(match.group(1))
        return mes if 1 <= mes <= 12 else None

    return None


def preparar_normales(df_normales: pd.DataFrame, variable: str, mes: int) -> float:
    if df_normales.empty:
        raise AemetError("normal climática vacía")

    var = VARIABLES[variable]
    col_valor = buscar_columna(df_normales, var["normal"])
    if col_valor is None:
        raise AemetError(f"no encuentro columna normal para {variable}; columnas: {list(df_normales.columns)}")

    # Caso habitual: una fila por mes con columna 'mes' o 'fecha'.
    col_mes = buscar_columna(df_normales, ["mes", "fecha", "periodo", "mes_nombre"])
    if col_mes is not None:
        tmp = df_normales.copy()
        tmp["_mes"] = tmp[col_mes].apply(extraer_mes)
        tmp["_valor"] = tmp[col_valor].apply(a_float)
        fila = tmp.loc[(tmp["_mes"] == mes) & tmp["_valor"].notna()]
        if not fila.empty:
            return float(fila.iloc[0]["_valor"])

    # Alternativa defensiva: columnas con nombres de mes.
    mes_nombre = MESES[mes]
    posibles_columnas_mes = [mes_nombre, mes_nombre[:3], str(mes)]
    col_mes_valor = buscar_columna(df_normales, posibles_columnas_mes)
    if col_mes_valor is not None:
        # Si el dataframe tiene una fila por variable, intentamos localizar la variable.
        for _, row in df_normales.iterrows():
            texto_fila = " ".join(str(x).lower() for x in row.values)
            if variable in quitar_acentos_basico(texto_fila):
                return a_float(row[col_mes_valor])

    raise AemetError(f"no encuentro normal para el mes {mes}")


def preparar_diarios(df_diarios: pd.DataFrame, variable: str, fecha_ref: date) -> tuple[float, int, str]:
    if df_diarios.empty:
        raise AemetError("datos diarios vacíos")

    var = VARIABLES[variable]
    col_fecha = buscar_columna(df_diarios, ["fecha"])
    col_valor = buscar_columna(df_diarios, var["diario"])

    if col_fecha is None or col_valor is None:
        raise AemetError(
            f"no encuentro columnas diarias para {variable}; columnas: {list(df_diarios.columns)}"
        )

    tmp = df_diarios[[col_fecha, col_valor]].copy()
    tmp["fecha"] = pd.to_datetime(tmp[col_fecha], errors="coerce")
    tmp["valor"] = tmp[col_valor].apply(a_float)
    tmp = tmp.dropna(subset=["fecha", "valor"])
    tmp = tmp[
        (tmp["fecha"].dt.year == fecha_ref.year)
        & (tmp["fecha"].dt.month == fecha_ref.month)
    ]

    if tmp.empty:
        raise AemetError("no hay datos diarios válidos en el mes")

    if var["calculo"] == "media":
        observado = float(tmp["valor"].mean())
    else:
        observado = float(tmp["valor"].sum())

    dias = int(tmp["fecha"].dt.date.nunique())
    ultimo_dia = tmp["fecha"].dt.date.max().isoformat()
    return observado, dias, ultimo_dia


def crear_grafico(
    salida_graficos: Path,
    estacion: str,
    nombre: str,
    variable: str,
    fecha_ref: date,
    normal: float,
    observado: float,
    dias: int,
    ultimo_dia: str,
) -> str:
    var = VARIABLES[variable]
    unidad = var["unidad"]
    mes_nombre = MESES[fecha_ref.month]
    diferencia = observado - normal

    etiquetas = ["Normal\nclimática", f"Mes en curso\nhasta {ultimo_dia[8:10]}/{ultimo_dia[5:7]}"]
    valores = [normal, observado]

    plt.figure(figsize=(7.5, 5.2))
    barras = plt.bar(etiquetas, valores)
    plt.ylabel(unidad)
    plt.title(
        f"{var['titulo']} · {mes_nombre} {fecha_ref.year}\n"
        f"{nombre} ({estacion}) · {dias} días disponibles"
    )
    plt.grid(axis="y", alpha=0.25)

    for barra, valor in zip(barras, valores, strict=False):
        plt.text(
            barra.get_x() + barra.get_width() / 2,
            barra.get_height(),
            f"{valor:.1f} {unidad}",
            ha="center",
            va="bottom",
            fontsize=10,
        )

    signo = "+" if diferencia >= 0 else ""
    plt.figtext(
        0.5,
        0.02,
        f"Diferencia respecto a la normal mensual: {signo}{diferencia:.1f} {unidad}",
        ha="center",
        fontsize=9,
    )
    plt.tight_layout(rect=(0, 0.05, 1, 1))

    nombre_archivo = f"{estacion}_{variable}_{fecha_ref:%Y%m}.png"
    ruta = salida_graficos / nombre_archivo
    plt.savefig(ruta, dpi=160)
    plt.close()
    return f"graficos/{nombre_archivo}"


def procesar_estacion_variable(
    api_key: str,
    estacion: str,
    nombre: str,
    variable: str,
    fecha_ref: date,
    salida_graficos: Path,
) -> ResultadoVariable:
    titulo = VARIABLES[variable]["titulo"]
    unidad = VARIABLES[variable]["unidad"]

    try:
        normales = descargar_normales(api_key, estacion)
        normal = preparar_normales(normales, variable, fecha_ref.month)

        # Pausa suave para no castigar la API.
        time.sleep(0.25)

        diarios = descargar_diarios_mes(api_key, estacion, fecha_ref)
        observado, dias, ultimo_dia = preparar_diarios(diarios, variable, fecha_ref)

        grafico = crear_grafico(
            salida_graficos=salida_graficos,
            estacion=estacion,
            nombre=nombre,
            variable=variable,
            fecha_ref=fecha_ref,
            normal=normal,
            observado=observado,
            dias=dias,
            ultimo_dia=ultimo_dia,
        )

        diferencia = observado - normal
        return ResultadoVariable(
            estacion=estacion,
            nombre=nombre,
            variable=variable,
            titulo=titulo,
            unidad=unidad,
            estado="ok",
            mensaje="ok",
            normal=normal,
            observado=observado,
            diferencia=diferencia,
            dias=dias,
            ultimo_dia=ultimo_dia,
            grafico=grafico,
        )

    except Exception as exc:  # noqa: BLE001
        return ResultadoVariable(
            estacion=estacion,
            nombre=nombre,
            variable=variable,
            titulo=titulo,
            unidad=unidad,
            estado="sin_datos",
            mensaje=str(exc),
        )


def guardar_resumen_csv(resultados: list[ResultadoVariable], salida_datos: Path) -> Path:
    ruta = salida_datos / "resumen.csv"
    campos = [
        "estacion", "nombre", "variable", "titulo", "unidad", "estado", "mensaje",
        "normal", "observado", "diferencia", "dias", "ultimo_dia", "grafico",
    ]
    with ruta.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=campos)
        writer.writeheader()
        for r in resultados:
            writer.writerow({campo: getattr(r, campo) for campo in campos})
    return ruta


def generar_index_html(
    resultados: list[ResultadoVariable],
    salida: Path,
    fecha_ref: date,
    estaciones: list[tuple[str, str]],
    variables: list[str],
) -> Path:
    por_estacion: dict[str, list[ResultadoVariable]] = {}
    for r in resultados:
        por_estacion.setdefault(r.estacion, []).append(r)

    fecha_actualizacion = datetime.now().strftime("%d/%m/%Y %H:%M")
    mes_nombre = MESES[fecha_ref.month]

    css = """
    :root { color-scheme: light; }
    body {
      margin: 0;
      font-family: Arial, Helvetica, sans-serif;
      background: #f4f6f8;
      color: #1f2933;
    }
    .wrap {
      max-width: 1180px;
      margin: 0 auto;
      padding: 18px;
    }
    .cabecera {
      background: linear-gradient(135deg, #263544, #516575);
      color: white;
      border-radius: 18px;
      padding: 20px;
      margin-bottom: 18px;
      box-shadow: 0 8px 24px rgba(0,0,0,.12);
    }
    .cabecera h1 { margin: 0 0 8px 0; font-size: 26px; }
    .cabecera p { margin: 4px 0; opacity: .95; }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 16px;
    }
    .estacion {
      background: white;
      border-radius: 18px;
      padding: 16px;
      box-shadow: 0 6px 18px rgba(0,0,0,.08);
    }
    .estacion h2 {
      margin: 0 0 12px 0;
      font-size: 20px;
      border-bottom: 1px solid #e5e7eb;
      padding-bottom: 8px;
    }
    .codigo { color: #64748b; font-size: 14px; font-weight: normal; }
    .variable {
      border: 1px solid #e5e7eb;
      border-radius: 14px;
      margin: 12px 0;
      padding: 12px;
      background: #fbfdff;
    }
    .variable h3 { margin: 0 0 8px 0; font-size: 16px; }
    .datos {
      display: grid;
      grid-template-columns: 1fr 1fr 1fr;
      gap: 8px;
      margin-bottom: 10px;
      font-size: 13px;
    }
    .dato {
      background: #eef2f6;
      border-radius: 10px;
      padding: 8px;
      text-align: center;
    }
    .dato strong { display: block; font-size: 16px; margin-bottom: 2px; }
    img {
      width: 100%;
      height: auto;
      border-radius: 12px;
      border: 1px solid #e5e7eb;
      background: white;
    }
    .sin-datos {
      color: #9a3412;
      background: #fff7ed;
      border: 1px solid #fed7aa;
      border-radius: 12px;
      padding: 10px;
      font-size: 13px;
    }
    .pie {
      margin: 18px 0 0 0;
      color: #64748b;
      font-size: 13px;
      text-align: center;
    }
    """

    partes: list[str] = []
    partes.append("<!doctype html>")
    partes.append('<html lang="es">')
    partes.append("<head>")
    partes.append('<meta charset="utf-8">')
    partes.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    partes.append(f"<title>AEMET · Mes en curso vs normal · {html.escape(mes_nombre)} {fecha_ref.year}</title>")
    partes.append(f"<style>{css}</style>")
    partes.append("</head>")
    partes.append("<body>")
    partes.append('<div class="wrap">')
    partes.append('<section class="cabecera">')
    partes.append("<h1>AEMET · Mes en curso frente a normal climática</h1>")
    partes.append(f"<p><strong>Periodo:</strong> {html.escape(mes_nombre)} {fecha_ref.year}</p>")
    partes.append(f"<p><strong>Variables:</strong> {', '.join(html.escape(VARIABLES[v]['titulo']) for v in variables)}</p>")
    partes.append(f"<p><strong>Actualización del visor:</strong> {html.escape(fecha_actualizacion)}</p>")
    partes.append("</section>")
    partes.append('<main class="grid">')

    for estacion, nombre in estaciones:
        partes.append('<section class="estacion">')
        partes.append(f"<h2>{html.escape(nombre)} <span class='codigo'>{html.escape(estacion)}</span></h2>")

        resultados_estacion = {r.variable: r for r in por_estacion.get(estacion, [])}
        for variable in variables:
            r = resultados_estacion.get(variable)
            var = VARIABLES[variable]
            partes.append('<article class="variable">')
            partes.append(f"<h3>{html.escape(var['titulo'])}</h3>")
            if r and r.estado == "ok":
                unidad = html.escape(r.unidad)
                signo = "+" if (r.diferencia or 0) >= 0 else ""
                partes.append('<div class="datos">')
                partes.append(f"<div class='dato'><strong>{r.normal:.1f}</strong>Normal {unidad}</div>")
                partes.append(f"<div class='dato'><strong>{r.observado:.1f}</strong>Mes actual {unidad}</div>")
                partes.append(f"<div class='dato'><strong>{signo}{r.diferencia:.1f}</strong>Diferencia {unidad}</div>")
                partes.append("</div>")
                partes.append(f"<img src='{html.escape(r.grafico or '')}' alt='{html.escape(r.titulo)} en {html.escape(nombre)}'>")
                partes.append(f"<p class='codigo'>Último día disponible: {html.escape(r.ultimo_dia or '')} · Días usados: {r.dias}</p>")
            else:
                mensaje = r.mensaje if r else "sin resultado"
                partes.append(f"<div class='sin-datos'>Sin datos suficientes. {html.escape(mensaje)}</div>")
            partes.append("</article>")

        partes.append("</section>")

    partes.append("</main>")
    partes.append('<p class="pie">Fuente: AEMET OpenData. Elaboración automática para seguimiento del mes en curso.</p>')
    partes.append("</div>")
    partes.append("</body>")
    partes.append("</html>")

    ruta = salida / "index.html"
    ruta.write_text("\n".join(partes), encoding="utf-8")
    return ruta


def parse_fecha(texto: str | None) -> date:
    if not texto:
        return date.today()
    return datetime.strptime(texto, "%Y-%m-%d").date()


def seleccionar_estaciones(texto: str | None) -> list[tuple[str, str]]:
    if not texto:
        return ESTACIONES_VALENCIA

    pedidos = {x.strip().upper() for x in texto.split(",") if x.strip()}
    seleccionadas = [(cod, nom) for cod, nom in ESTACIONES_VALENCIA if cod.upper() in pedidos]
    if not seleccionadas:
        raise AemetError(f"No coincide ninguna estación con: {texto}")
    return seleccionadas


def seleccionar_variables(texto: str | None) -> list[str]:
    if not texto:
        return ["tmed", "tmax", "hr"]
    variables = [x.strip().lower() for x in texto.split(",") if x.strip()]
    invalidas = [v for v in variables if v not in VARIABLES]
    if invalidas:
        raise AemetError(f"Variables no válidas: {invalidas}. Válidas: {list(VARIABLES)}")
    return variables


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Genera panel AEMET diario: mes en curso vs normal climática para estaciones de Valencia."
    )
    parser.add_argument("--fecha", default=None, help="Fecha de referencia YYYY-MM-DD. Por defecto, hoy.")
    parser.add_argument("--api-key", default=None, help="API key de AEMET. También puede usarse AEMET_API_KEY.")
    parser.add_argument("--salida", default="docs/clima", help="Carpeta de salida web. Por defecto: docs/clima")
    parser.add_argument("--estaciones", default=None, help="Códigos separados por coma. Ejemplo: 8416,8293X")
    parser.add_argument("--variables", default=None, help="Variables separadas por coma. Por defecto: tmed,tmax,hr")
    args = parser.parse_args()

    try:
        api_key = api_key_desde_entorno(args.api_key)
        fecha_ref = parse_fecha(args.fecha)
        estaciones = seleccionar_estaciones(args.estaciones)
        variables = seleccionar_variables(args.variables)

        salida = Path(args.salida)
        salida_graficos = salida / "graficos"
        salida_datos = salida / "datos"
        salida_graficos.mkdir(parents=True, exist_ok=True)
        salida_datos.mkdir(parents=True, exist_ok=True)

        resultados: list[ResultadoVariable] = []

        total = len(estaciones) * len(variables)
        contador = 0
        print(f"Procesando {len(estaciones)} estaciones y {len(variables)} variables ({total} consultas dobles aprox.)")

        for estacion, nombre in estaciones:
            print(f"\n{estacion} - {nombre}")
            for variable in variables:
                contador += 1
                print(f"  [{contador}/{total}] {variable}...", end=" ", flush=True)
                resultado = procesar_estacion_variable(
                    api_key=api_key,
                    estacion=estacion,
                    nombre=nombre,
                    variable=variable,
                    fecha_ref=fecha_ref,
                    salida_graficos=salida_graficos,
                )
                resultados.append(resultado)
                print(resultado.estado)

                # Pausa para evitar ráfagas excesivas.
                time.sleep(0.35)

        csv_path = guardar_resumen_csv(resultados, salida_datos)
        html_path = generar_index_html(resultados, salida, fecha_ref, estaciones, variables)

        ok = sum(1 for r in resultados if r.estado == "ok")
        sin_datos = len(resultados) - ok

        print("\nResultado")
        print("---------")
        print(f"OK: {ok}")
        print(f"Sin datos: {sin_datos}")
        print(f"HTML: {html_path}")
        print(f"CSV: {csv_path}")
        return 0

    except AemetError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except requests.RequestException as exc:
        print(f"ERROR de red: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
