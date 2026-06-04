#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AEMET OpenData · Evolución diaria del mes actual frente a normal climática oficial.

Objetivo del visor:
  - Para cada estación elegida, descargar la temperatura media diaria del mes en curso.
  - Calcular la media acumulada del mes actual con los días disponibles.
  - Descargar la normal climática oficial AEMET 1991-2020 para esa estación.
  - Dibujar la evolución diaria del mes actual y dos referencias horizontales:
      1) media acumulada del mes actual;
      2) normal climática oficial 1991-2020 del mismo mes.

Fuentes AEMET OpenData usadas:
  1) Valores climatológicos diarios:
     /valores/climatologicos/diarios/datos/fechaini/{fecha}/fechafin/{fecha}/estacion/{idema}
  2) Climatologías normales 1991-2020:
     /valores/climatologicos/normales/estacion/{idema}

Salida:
  docs/clima/index.html
  docs/clima/graficos/*.png
  docs/clima/datos/resumen.csv
  docs/clima/datos/diarios_actual.csv
  docs/clima/datos/normales.csv
  docs/clima/datos/aemet_clima.json
  docs/clima/datos/cache/*.json

Ejemplos:
  python scripts/aemet_mes_en_curso_vs_normal.py --estaciones 8416,8293X --variables tmed
  python scripts/aemet_mes_en_curso_vs_normal.py --estaciones 8416,8293X --variables tmed --fecha 2026-05-31
  python scripts/aemet_mes_en_curso_vs_normal.py --estaciones 8416,8293X --variables tmed,tmax,tmin,hr

Notas:
  - La normal climática es la normal oficial de AEMET 1991-2020, no una media propia de años recientes.
  - Los datos diarios climatológicos pueden publicarse con retraso. Si AEMET aún no devuelve días
    del mes actual, el visor lo indicará como sin datos actuales.
  - Para evitar 429, el script solo hace 2 llamadas por estación: diarios + normales.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import os
import re
import sys
import time
from calendar import monthrange
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import requests

BASE_API = "https://opendata.aemet.es/opendata/api"
TZ_LOCAL = ZoneInfo("Europe/Madrid")

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
        "titulo_diario": "Temperatura media diaria",
        "titulo_resumen": "Temperatura media mensual",
        "unidad": "°C",
        "diario": ["tmed", "temperatura_media", "temp_media", "temperatura media", "ta_med", "tm"],
        "normal": [
            "tm_mes_md", "t", "tmed", "temperatura_media", "temp_media",
            "temperatura media", "temperatura_media_mensual", "media_temperatura_media",
        ],
    },
    "tmax": {
        "titulo_diario": "Temperatura máxima diaria",
        "titulo_resumen": "Temperatura máxima media mensual",
        "unidad": "°C",
        "diario": ["tmax", "temperatura_maxima", "temperatura_máxima", "temp_max", "maxima", "máxima"],
        "normal": [
            "tm_max_md", "tm_max", "tmax", "temperatura_maxima_media", "temperatura_máxima_media",
            "media_temperaturas_maximas", "media_temperaturas_máximas", "temperatura_maxima",
            "temperatura_máxima", "temp_max", "maxima_media", "máxima_media",
        ],
    },
    "tmin": {
        "titulo_diario": "Temperatura mínima diaria",
        "titulo_resumen": "Temperatura mínima media mensual",
        "unidad": "°C",
        "diario": ["tmin", "temperatura_minima", "temperatura_mínima", "temp_min", "minima", "mínima"],
        "normal": [
            "tm_min_md", "tm_min", "tmin", "temperatura_minima_media", "temperatura_mínima_media",
            "media_temperaturas_minimas", "media_temperaturas_mínimas", "temperatura_minima",
            "temperatura_mínima", "temp_min", "minima_media", "mínima_media",
        ],
    },
    "hr": {
        "titulo_diario": "Humedad relativa media diaria",
        "titulo_resumen": "Humedad relativa media mensual",
        "unidad": "%",
        "diario": [
            "hrmedia", "hr_media", "hrmed", "hr_med", "hr", "humedad_relativa_media",
            "humedad media", "humedad_media", "humedad_relativa", "humedad",
        ],
        "normal": [
            "hr_md", "h", "hrmedia", "hr_media", "hrmed", "hr_med", "hr",
            "humedad_relativa_media", "humedad media", "humedad_media", "humedad_relativa", "humedad",
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
    anio_actual: int
    mes: int
    estado: str
    mensaje: str
    media_actual: float | None = None
    normal_1991_2020: float | None = None
    diferencia: float | None = None
    dias_actual: int | None = None
    ultimo_dia_actual: str | None = None
    grafico: str | None = None


def quitar_acentos_basico(texto: str) -> str:
    return (
        texto.replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u")
        .replace("Á", "a").replace("É", "e").replace("Í", "i").replace("Ó", "o").replace("Ú", "u")
        .replace("à", "a").replace("è", "e").replace("ì", "i").replace("ò", "o").replace("ù", "u")
        .replace("À", "a").replace("È", "e").replace("Ì", "i").replace("Ò", "o").replace("Ù", "u")
        .replace("ï", "i").replace("ü", "u").replace("ñ", "n").replace("ç", "c")
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


def redondear(valor: float | None, ndigits: int = 2) -> float | None:
    if valor is None:
        return None
    if isinstance(valor, float) and (math.isnan(valor) or math.isinf(valor)):
        return None
    return round(float(valor), ndigits)


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
        raise AemetError("Falta la API key. Configura AEMET_API_KEY como secreto de GitHub.")
    return api_key


def descargar_endpoint(endpoint: str, api_key: str, cache_path: Path | None = None, usar_cache: bool = False) -> Any:
    """Descarga endpoint AEMET en dos pasos: descriptor -> URL de datos."""
    if usar_cache and cache_path and cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    url = f"{BASE_API}{endpoint}"
    headers = {
        "api_key": api_key,
        "accept": "application/json",
        "cache-control": "no-cache",
    }

    ultimo_error = ""
    for intento in range(1, 4):
        resp = requests.get(url, headers=headers, timeout=60)

        # Compatibilidad con ejemplos antiguos donde la API key se pasa como parámetro.
        if resp.status_code in {401, 403}:
            resp = requests.get(url, params={"api_key": api_key}, timeout=60)

        if resp.status_code == 429:
            ultimo_error = "AEMET devuelve 429: demasiadas peticiones"
            espera = 10 * intento
            print(f"    429 de AEMET. Reintento {intento}/3 en {espera}s...", flush=True)
            time.sleep(espera)
            continue

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

        data = leer_json(datos_resp)
        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data

    raise AemetError(ultimo_error or "No se pudo descargar el endpoint de AEMET")


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
    return normalizar_columnas(pd.DataFrame(data))


def descargar_normales(api_key: str, estacion: str, cache_dir: Path) -> pd.DataFrame:
    endpoint = f"/valores/climatologicos/normales/estacion/{estacion}"
    cache_path = cache_dir / f"normal_1991_2020_{estacion}.json"
    data = descargar_endpoint(endpoint, api_key, cache_path=cache_path, usar_cache=True)
    return normalizar_columnas(pd.DataFrame(data))


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

    match = re.search(r"\d{4}[-/](\d{1,2})", texto)
    if match:
        mes = int(match.group(1))
        return mes if 1 <= mes <= 12 else None

    return None


def normal_1991_2020(df_normales: pd.DataFrame, variable: str, mes: int) -> tuple[float, pd.DataFrame]:
    if df_normales.empty:
        raise AemetError("normal climática vacía")

    var = VARIABLES[variable]
    col_valor = buscar_columna(df_normales, var["normal"])
    if col_valor is None:
        raise AemetError(
            f"no encuentro columna de normal 1991-2020 para {variable}; columnas: {list(df_normales.columns)}"
        )

    # Caso más habitual: una fila por mes con una columna 'mes', 'fecha' o 'periodo'.
    col_mes = buscar_columna(df_normales, ["mes", "fecha", "periodo", "mes_nombre", "nombre_mes"])
    if col_mes is not None:
        tmp = df_normales.copy()
        tmp["_mes"] = tmp[col_mes].apply(extraer_mes)
        tmp["_valor"] = tmp[col_valor].apply(a_float)
        fila = tmp.loc[(tmp["_mes"] == mes) & tmp["_valor"].notna()]
        if not fila.empty:
            valor = float(fila.iloc[0]["_valor"])
            return valor, tmp

    # Alternativa: columnas con nombres de mes y una fila por variable.
    mes_nombre = MESES[mes]
    posibles_columnas_mes = [mes_nombre, mes_nombre[:3], str(mes)]
    col_mes_valor = buscar_columna(df_normales, posibles_columnas_mes)
    if col_mes_valor is not None:
        for _, row in df_normales.iterrows():
            texto_fila = quitar_acentos_basico(" ".join(str(x).lower() for x in row.values))
            if variable in texto_fila or var["titulo_resumen"].lower().split()[0] in texto_fila:
                valor = a_float(row[col_mes_valor])
                if not math.isnan(valor):
                    return float(valor), df_normales

    raise AemetError(f"no encuentro normal climática 1991-2020 para {MESES[mes].lower()}")


def serie_diaria_actual(df_diarios: pd.DataFrame, variable: str, fecha_ref: date) -> tuple[pd.DataFrame, float, int, str]:
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
    tmp = tmp.sort_values("fecha")

    if tmp.empty:
        raise AemetError("no hay datos diarios válidos en el mes actual")

    tmp["dia"] = tmp["fecha"].dt.day
    media_actual = float(tmp["valor"].mean())
    dias = int(tmp["fecha"].dt.date.nunique())
    ultimo_dia = tmp["fecha"].dt.date.max().isoformat()
    return tmp[["fecha", "dia", "valor"]], media_actual, dias, ultimo_dia


def crear_grafico(
    salida_graficos: Path,
    estacion: str,
    nombre: str,
    variable: str,
    fecha_ref: date,
    serie: pd.DataFrame,
    media_actual: float,
    normal: float,
    dias: int,
    ultimo_dia: str,
) -> str:
    var = VARIABLES[variable]
    unidad = var["unidad"]
    mes_nombre = MESES[fecha_ref.month]
    _, dias_mes = monthrange(fecha_ref.year, fecha_ref.month)

    plt.figure(figsize=(10.5, 5.8))
    plt.plot(serie["dia"], serie["valor"], marker="o", linewidth=2, label=f"{fecha_ref.year} · dato diario")
    plt.axhline(media_actual, linestyle="--", linewidth=1.8, label=f"Media acumulada {fecha_ref.year}: {media_actual:.1f} {unidad}")
    plt.axhline(normal, linestyle=":", linewidth=2.2, label=f"Normal AEMET 1991-2020: {normal:.1f} {unidad}")

    plt.title(f"{var['titulo_diario']} · {mes_nombre} {fecha_ref.year}\n{nombre} ({estacion})")
    plt.xlabel("Día del mes")
    plt.ylabel(unidad)
    plt.xlim(1, dias_mes)
    plt.xticks(range(1, dias_mes + 1, 2))
    plt.grid(alpha=0.25)
    plt.legend(loc="best", fontsize=9)

    diferencia = media_actual - normal
    signo = "+" if diferencia >= 0 else ""
    plt.figtext(
        0.5,
        0.01,
        f"Último día disponible: {ultimo_dia} · Días usados: {dias} · "
        f"Anomalía provisional frente a normal: {signo}{diferencia:.1f} {unidad}",
        ha="center",
        fontsize=9,
    )
    plt.tight_layout(rect=(0, 0.05, 1, 1))

    nombre_archivo = f"{estacion}_{variable}_{fecha_ref:%Y%m}_normal_1991_2020.png"
    ruta = salida_graficos / nombre_archivo
    plt.savefig(ruta, dpi=160)
    plt.close()
    return f"graficos/{nombre_archivo}"


def procesar_estacion(
    api_key: str,
    estacion: str,
    nombre: str,
    variables: list[str],
    fecha_ref: date,
    salida_graficos: Path,
    salida_datos: Path,
) -> tuple[list[ResultadoVariable], list[dict[str, Any]], list[dict[str, Any]]]:
    resultados: list[ResultadoVariable] = []
    registros_diarios: list[dict[str, Any]] = []
    registros_normales: list[dict[str, Any]] = []

    df_diarios: pd.DataFrame | None = None
    df_normales: pd.DataFrame | None = None

    try:
        df_diarios = descargar_diarios_mes(api_key, estacion, fecha_ref)
    except Exception as exc:  # noqa: BLE001
        df_diarios = None
        error_diarios = str(exc)
    else:
        error_diarios = ""

    # Pausa suave entre productos.
    time.sleep(0.8)

    try:
        df_normales = descargar_normales(api_key, estacion, salida_datos / "cache")
    except Exception as exc:  # noqa: BLE001
        df_normales = None
        error_normales = str(exc)
    else:
        error_normales = ""

    for variable in variables:
        var = VARIABLES[variable]
        try:
            if df_diarios is None:
                raise AemetError(f"sin datos diarios: {error_diarios}")
            if df_normales is None:
                raise AemetError(f"sin normal climática: {error_normales}")

            serie, media_actual, dias, ultimo_dia = serie_diaria_actual(df_diarios, variable, fecha_ref)
            normal, df_normales_preparado = normal_1991_2020(df_normales, variable, fecha_ref.month)

            for _, row in serie.iterrows():
                registros_diarios.append({
                    "estacion": estacion,
                    "nombre": nombre,
                    "variable": variable,
                    "fecha": row["fecha"].date().isoformat(),
                    "dia": int(row["dia"]),
                    "valor": redondear(float(row["valor"]), 2),
                })

            registros_normales.append({
                "estacion": estacion,
                "nombre": nombre,
                "variable": variable,
                "mes": fecha_ref.month,
                "mes_nombre": MESES[fecha_ref.month],
                "normal_1991_2020": redondear(normal, 2),
            })

            grafico = crear_grafico(
                salida_graficos=salida_graficos,
                estacion=estacion,
                nombre=nombre,
                variable=variable,
                fecha_ref=fecha_ref,
                serie=serie,
                media_actual=media_actual,
                normal=normal,
                dias=dias,
                ultimo_dia=ultimo_dia,
            )

            diferencia = media_actual - normal
            resultados.append(ResultadoVariable(
                estacion=estacion,
                nombre=nombre,
                variable=variable,
                titulo=var["titulo_resumen"],
                unidad=var["unidad"],
                anio_actual=fecha_ref.year,
                mes=fecha_ref.month,
                estado="ok",
                mensaje="ok",
                media_actual=redondear(media_actual, 2),
                normal_1991_2020=redondear(normal, 2),
                diferencia=redondear(diferencia, 2),
                dias_actual=dias,
                ultimo_dia_actual=ultimo_dia,
                grafico=grafico,
            ))

        except Exception as exc:  # noqa: BLE001
            resultados.append(ResultadoVariable(
                estacion=estacion,
                nombre=nombre,
                variable=variable,
                titulo=var["titulo_resumen"],
                unidad=var["unidad"],
                anio_actual=fecha_ref.year,
                mes=fecha_ref.month,
                estado="sin_datos",
                mensaje=str(exc),
            ))

    return resultados, registros_diarios, registros_normales


def guardar_csv_dicts(ruta: Path, filas: list[dict[str, Any]], campos: list[str]) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with ruta.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=campos)
        writer.writeheader()
        for fila in filas:
            writer.writerow({campo: fila.get(campo) for campo in campos})


def guardar_salidas_datos(
    resultados: list[ResultadoVariable],
    diarios: list[dict[str, Any]],
    normales: list[dict[str, Any]],
    salida_datos: Path,
    fecha_ref: date,
) -> None:
    resumen = [asdict(r) for r in resultados]
    campos_resumen = list(resumen[0].keys()) if resumen else [
        "estacion", "nombre", "variable", "titulo", "unidad", "anio_actual", "mes", "estado", "mensaje",
        "media_actual", "normal_1991_2020", "diferencia", "dias_actual", "ultimo_dia_actual", "grafico",
    ]
    guardar_csv_dicts(salida_datos / "resumen.csv", resumen, campos_resumen)
    guardar_csv_dicts(
        salida_datos / "diarios_actual.csv",
        diarios,
        ["estacion", "nombre", "variable", "fecha", "dia", "valor"],
    )
    guardar_csv_dicts(
        salida_datos / "normales.csv",
        normales,
        ["estacion", "nombre", "variable", "mes", "mes_nombre", "normal_1991_2020"],
    )

    payload = {
        "generado": datetime.now(TZ_LOCAL).isoformat(),
        "periodo": {"anio": fecha_ref.year, "mes": fecha_ref.month, "mes_nombre": MESES[fecha_ref.month]},
        "fuente": "AEMET OpenData",
        "referencia": "Normal climática oficial AEMET 1991-2020",
        "resultados": resumen,
        "diarios_actual": diarios,
        "normales": normales,
    }
    (salida_datos / "aemet_clima.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def generar_index_html(resultados: list[ResultadoVariable], salida: Path, fecha_ref: date, estaciones: list[tuple[str, str]], variables: list[str]) -> Path:
    por_estacion: dict[str, list[ResultadoVariable]] = {}
    for r in resultados:
        por_estacion.setdefault(r.estacion, []).append(r)

    fecha_actualizacion = datetime.now(TZ_LOCAL).strftime("%d/%m/%Y %H:%M")
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
      grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
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
      line-height: 1.45;
    }
    @media (max-width: 640px) {
      .wrap { padding: 10px; }
      .grid { grid-template-columns: 1fr; }
      .datos { grid-template-columns: 1fr; }
      .cabecera h1 { font-size: 21px; }
    }
    """

    partes: list[str] = []
    partes.append("<!doctype html>")
    partes.append('<html lang="es">')
    partes.append("<head>")
    partes.append('<meta charset="utf-8">')
    partes.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    partes.append(f"<title>AEMET · Evolución diaria vs normal 1991-2020 · {html.escape(mes_nombre)} {fecha_ref.year}</title>")
    partes.append(f"<style>{css}</style>")
    partes.append("</head>")
    partes.append("<body>")
    partes.append('<div class="wrap">')
    partes.append('<section class="cabecera">')
    partes.append("<h1>AEMET · Evolución diaria frente a normal climática</h1>")
    partes.append(f"<p><strong>Periodo:</strong> {html.escape(mes_nombre)} {fecha_ref.year}</p>")
    partes.append("<p><strong>Referencia:</strong> normal climática oficial AEMET 1991-2020 del mismo mes</p>")
    partes.append(f"<p><strong>Variables:</strong> {', '.join(html.escape(VARIABLES[v]['titulo_resumen']) for v in variables)}</p>")
    partes.append(f"<p><strong>Actualización del visor:</strong> {html.escape(fecha_actualizacion)} hora peninsular</p>")
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
            partes.append(f"<h3>{html.escape(var['titulo_resumen'])}</h3>")
            if r and r.estado == "ok":
                unidad = html.escape(r.unidad)
                signo = "+" if (r.diferencia or 0) >= 0 else ""
                partes.append('<div class="datos">')
                partes.append(f"<div class='dato'><strong>{r.media_actual:.1f}</strong>Media actual {unidad}</div>")
                partes.append(f"<div class='dato'><strong>{r.normal_1991_2020:.1f}</strong>Normal 1991-2020 {unit}</div>".replace("{unit}", unidad))
                partes.append(f"<div class='dato'><strong>{signo}{r.diferencia:.1f}</strong>Anomalía {unidad}</div>")
                partes.append("</div>")
                partes.append(f"<img src='{html.escape(r.grafico or '')}' alt='{html.escape(r.titulo)} en {html.escape(nombre)}'>")
                partes.append(f"<p class='codigo'>Último día disponible: {html.escape(r.ultimo_dia_actual or '')} · Días usados: {r.dias_actual}</p>")
            else:
                mensaje = r.mensaje if r else "sin resultado"
                partes.append(f"<div class='sin-datos'>Sin datos suficientes. {html.escape(mensaje)}</div>")
            partes.append("</article>")

        partes.append("</section>")

    partes.append("</main>")
    partes.append(
        '<p class="pie">Fuente: AEMET OpenData. Elaboración automática. '
        'La línea de referencia usa la normal climática oficial AEMET 1991-2020; '
        'la media actual es provisional y se calcula con los días diarios disponibles del mes en curso.</p>'
    )
    partes.append("</div>")
    partes.append("</body>")
    partes.append("</html>")

    ruta = salida / "index.html"
    ruta.write_text("\n".join(partes), encoding="utf-8")
    return ruta


def parse_fecha(texto: str | None) -> date:
    if not texto:
        return datetime.now(TZ_LOCAL).date()
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
        return ["tmed"]
    variables = [x.strip().lower() for x in texto.split(",") if x.strip()]
    invalidas = [v for v in variables if v not in VARIABLES]
    if invalidas:
        raise AemetError(f"Variables no válidas: {invalidas}. Válidas: {list(VARIABLES)}")
    return variables


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Genera visor AEMET: evolución diaria del mes actual frente a normal climática oficial 1991-2020."
    )
    parser.add_argument("--fecha", default=None, help="Fecha de referencia YYYY-MM-DD. Por defecto, hoy en Europe/Madrid.")
    parser.add_argument("--api-key", default=None, help="API key de AEMET. También puede usarse AEMET_API_KEY.")
    parser.add_argument("--salida", default="docs/clima", help="Carpeta de salida web. Por defecto: docs/clima")
    parser.add_argument("--estaciones", default=None, help="Códigos separados por coma. Ejemplo: 8416,8293X")
    parser.add_argument("--variables", default="tmed", help="Variables separadas por coma. Por defecto: tmed")
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
        diarios: list[dict[str, Any]] = []
        normales: list[dict[str, Any]] = []

        print(f"Procesando {len(estaciones)} estaciones · variables: {', '.join(variables)} · periodo: {MESES[fecha_ref.month]} {fecha_ref.year}")
        print("Referencia: normal climática oficial AEMET 1991-2020")

        for i, (estacion, nombre) in enumerate(estaciones, start=1):
            print(f"\n[{i}/{len(estaciones)}] {estacion} - {nombre}", flush=True)
            res_est, diarios_est, normales_est = procesar_estacion(
                api_key=api_key,
                estacion=estacion,
                nombre=nombre,
                variables=variables,
                fecha_ref=fecha_ref,
                salida_graficos=salida_graficos,
                salida_datos=salida_datos,
            )
            resultados.extend(res_est)
            diarios.extend(diarios_est)
            normales.extend(normales_est)
            for r in res_est:
                print(f"  {r.variable}: {r.estado} · {r.mensaje}", flush=True)
            time.sleep(1.2)

        guardar_salidas_datos(resultados, diarios, normales, salida_datos, fecha_ref)
        html_path = generar_index_html(resultados, salida, fecha_ref, estaciones, variables)

        ok = sum(1 for r in resultados if r.estado == "ok")
        sin_datos = len(resultados) - ok

        print("\nResultado")
        print("---------")
        print(f"OK: {ok}")
        print(f"Sin datos: {sin_datos}")
        print(f"HTML: {html_path}")
        print(f"CSV: {salida_datos / 'resumen.csv'}")
        return 0

    except AemetError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except requests.RequestException as exc:
        print(f"ERROR de red: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
