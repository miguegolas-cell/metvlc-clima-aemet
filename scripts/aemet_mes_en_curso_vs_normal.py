#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AEMET OpenData · Evolución diaria del mes actual frente a media histórica mensual propia.

Qué genera:
  - Para cada estación elegida, descarga la evolución diaria del mes en curso/año actual.
  - Calcula la media mensual histórica del mismo mes usando climatologías mensuales/anuales
    de años anteriores al año actual.
  - Dibuja un gráfico de línea con la evolución diaria actual y una línea horizontal con
    la media mensual histórica.

Fuentes AEMET OpenData usadas:
  1) Climatologías diarias:
     /valores/climatologicos/diarios/datos/fechaini/{fecha}/fechafin/{fecha}/estacion/{idema}
  2) Climatologías mensuales/anuales:
     /valores/climatologicos/mensualesanuales/datos/anioini/{anio}/aniofin/{anio}/estacion/{idema}

Salida:
  docs/clima/index.html
  docs/clima/graficos/*.png
  docs/clima/datos/resumen.csv
  docs/clima/datos/historico_mensual.csv
  docs/clima/datos/diarios_actual.csv
  docs/clima/datos/aemet_clima.json
  docs/clima/datos/cache/*.json

Ejemplos:
  python scripts/aemet_mes_en_curso_vs_normal.py --estaciones 8416,8293X --variables tmed --anios-historico 10
  python scripts/aemet_mes_en_curso_vs_normal.py --estaciones 8416,8293X --fecha 2026-05-31 --variables tmed --anios-historico 10
  python scripts/aemet_mes_en_curso_vs_normal.py --estaciones 8416,8293X --variables tmed,tmax,hr --desde-anio 2016

Notas:
  - La media histórica de este visor NO es una normal climática oficial. Es una media propia
    calculada con los años anteriores disponibles en AEMET para la estación y mes elegidos.
  - El dato diario del mes en curso puede publicarse con retraso. Si AEMET aún no devuelve
    días del mes actual, el visor lo indicará como "sin datos actuales".
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

VARIABLES = {
    "tmed": {
        "titulo_diario": "Temperatura media diaria",
        "titulo_resumen": "Temperatura media mensual",
        "unidad": "°C",
        "diario": ["tmed", "temperatura_media", "temp_media", "temperatura media"],
        "mensual": ["tm_mes", "tmed", "temperatura_media", "temp_media", "temperatura media", "tm_mes_md"],
    },
    "tmax": {
        "titulo_diario": "Temperatura máxima diaria",
        "titulo_resumen": "Temperatura máxima media mensual",
        "unidad": "°C",
        "diario": ["tmax", "temperatura_maxima", "temperatura_máxima", "temp_max", "maxima", "máxima"],
        "mensual": ["tm_max", "tmax", "temperatura_maxima", "temperatura_máxima", "temp_max", "ta_max"],
    },
    "tmin": {
        "titulo_diario": "Temperatura mínima diaria",
        "titulo_resumen": "Temperatura mínima media mensual",
        "unidad": "°C",
        "diario": ["tmin", "temperatura_minima", "temperatura_mínima", "temp_min", "minima", "mínima"],
        "mensual": ["tm_min", "tmin", "temperatura_minima", "temperatura_mínima", "temp_min", "ta_min"],
    },
    "hr": {
        "titulo_diario": "Humedad relativa media diaria",
        "titulo_resumen": "Humedad relativa media mensual",
        "unidad": "%",
        "diario": [
            "hrmedia", "hr_media", "hrmed", "hr_med", "hr", "humedad_relativa_media",
            "humedad media", "humedad_media", "humedad_relativa", "humedad",
        ],
        "mensual": [
            "hr_media", "hrmedia", "hr_med", "hr_md", "hr", "humedad_relativa_media",
            "humedad media", "humedad_media", "humedad_relativa", "humedad",
        ],
    },
}


class AemetError(RuntimeError):
    pass


@dataclass
class RegistroHistorico:
    estacion: str
    nombre: str
    variable: str
    anio: int
    mes: int
    media_mensual: float | None
    estado: str
    mensaje: str


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
    media_historica: float | None = None
    diferencia: float | None = None
    dias_actual: int | None = None
    ultimo_dia_actual: str | None = None
    anios_historicos_usados: int | None = None
    periodo_historico: str | None = None
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
        raise AemetError(
            "Falta la API key. En GitHub debe existir el secreto AEMET_API_KEY. "
            "En PowerShell puedes usar: $env:AEMET_API_KEY=\"TU_API_KEY_DE_AEMET\""
        )
    return api_key


def descargar_endpoint(endpoint: str, api_key: str, intentos: int = 4) -> Any:
    """Descarga AEMET en dos pasos: descriptor -> URL de datos, con reintentos."""
    url = f"{BASE_API}{endpoint}"
    headers = {"api_key": api_key, "accept": "application/json", "cache-control": "no-cache"}

    espera = 8
    ultimo_error: Exception | None = None
    for intento in range(1, intentos + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=60)
            if resp.status_code in {401, 403}:
                resp = requests.get(url, params={"api_key": api_key}, timeout=60)
            if resp.status_code == 429:
                if intento < intentos:
                    print(f"      AEMET 429. Pausa {espera}s y reintento {intento + 1}/{intentos}.")
                    time.sleep(espera)
                    espera *= 2
                    continue
                raise AemetError("AEMET devuelve 429: demasiadas peticiones.")

            resp.raise_for_status()
            descriptor = leer_json(resp)
            if not isinstance(descriptor, dict) or "datos" not in descriptor:
                raise AemetError(f"Respuesta inesperada de AEMET: {descriptor}")

            datos_resp = requests.get(descriptor["datos"], timeout=60)
            datos_resp.raise_for_status()
            return leer_json(datos_resp)

        except (requests.RequestException, AemetError) as exc:
            ultimo_error = exc
            if intento < intentos:
                print(f"      Error temporal: {exc}. Pausa {espera}s y reintento {intento + 1}/{intentos}.")
                time.sleep(espera)
                espera *= 2
                continue
            break

    raise AemetError(str(ultimo_error))


def rango_mes(anio: int, mes: int, dia_final: int | None = None) -> tuple[date, date]:
    ultimo = monthrange(anio, mes)[1]
    if dia_final is not None:
        ultimo = min(max(1, dia_final), ultimo)
    return date(anio, mes, 1), date(anio, mes, ultimo)


def descargar_diarios_mes(api_key: str, estacion: str, anio: int, mes: int, dia_final: int | None = None) -> pd.DataFrame:
    inicio, fin = rango_mes(anio, mes, dia_final)
    fecha_ini = f"{inicio.isoformat()}T00:00:00UTC"
    fecha_fin = f"{fin.isoformat()}T23:59:59UTC"
    endpoint = (
        "/valores/climatologicos/diarios/datos/"
        f"fechaini/{fecha_ini}/"
        f"fechafin/{fecha_fin}/"
        f"estacion/{estacion}"
    )
    data = descargar_endpoint(endpoint, api_key)
    return normalizar_columnas(pd.DataFrame(data))


def descargar_mensuales_anuales(api_key: str, estacion: str, anio_ini: int, anio_fin: int) -> pd.DataFrame:
    endpoint = (
        "/valores/climatologicos/mensualesanuales/datos/"
        f"anioini/{anio_ini}/"
        f"aniofin/{anio_fin}/"
        f"estacion/{estacion}"
    )
    data = descargar_endpoint(endpoint, api_key)
    return normalizar_columnas(pd.DataFrame(data))


def cargar_o_descargar_mensuales(
    api_key: str,
    estacion: str,
    anio_ini: int,
    anio_fin: int,
    cache_dir: Path,
    forzar_descarga: bool = False,
) -> pd.DataFrame:
    cache_file = cache_dir / f"mensuales_{estacion}_{anio_ini}_{anio_fin}.json"
    if cache_file.exists() and not forzar_descarga:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        return normalizar_columnas(pd.DataFrame(data))

    df = descargar_mensuales_anuales(api_key, estacion, anio_ini, anio_fin)
    cache_file.write_text(df.to_json(orient="records", force_ascii=False, indent=2), encoding="utf-8")
    return df


def preparar_serie_diaria(df_diarios: pd.DataFrame, variable: str, anio: int, mes: int) -> pd.DataFrame:
    if df_diarios.empty:
        raise AemetError("datos diarios vacíos")

    col_fecha = buscar_columna(df_diarios, ["fecha"])
    col_valor = buscar_columna(df_diarios, VARIABLES[variable]["diario"])
    if col_fecha is None or col_valor is None:
        raise AemetError(f"no encuentro columnas diarias para {variable}; columnas: {list(df_diarios.columns)}")

    tmp = df_diarios[[col_fecha, col_valor]].copy()
    tmp["fecha"] = pd.to_datetime(tmp[col_fecha], errors="coerce")
    tmp["valor"] = tmp[col_valor].apply(a_float)
    tmp = tmp.dropna(subset=["fecha", "valor"])
    tmp = tmp[(tmp["fecha"].dt.year == anio) & (tmp["fecha"].dt.month == mes)]
    if tmp.empty:
        raise AemetError("no hay datos diarios válidos en el mes actual")

    tmp = tmp.sort_values("fecha")
    tmp["dia"] = tmp["fecha"].dt.day
    return tmp[["fecha", "dia", "valor"]]


def extraer_anio_mes(row: pd.Series, df: pd.DataFrame) -> tuple[int | None, int | None]:
    col_fecha = buscar_columna(df, ["fecha", "periodo"])
    if col_fecha is not None:
        texto = str(row[col_fecha]).strip().lower()
        match = re.search(r"(\d{4})[-/](\d{1,2})", texto)
        if match:
            return int(match.group(1)), int(match.group(2))
        match = re.search(r"(\d{4})", texto)
        if match and "an" not in texto:
            return int(match.group(1)), None

    col_anio = buscar_columna(df, ["anio", "año", "year"])
    col_mes = buscar_columna(df, ["mes", "month"])
    anio = None
    mes = None
    if col_anio is not None:
        try:
            anio = int(float(str(row[col_anio]).replace(",", ".")))
        except Exception:  # noqa: BLE001
            anio = None
    if col_mes is not None:
        try:
            mes = int(float(str(row[col_mes]).replace(",", ".")))
        except Exception:  # noqa: BLE001
            mes = None
    return anio, mes


def preparar_historico_mensual(
    df_mensual: pd.DataFrame,
    estacion: str,
    nombre: str,
    variable: str,
    anio_actual: int,
    mes_objetivo: int,
    anios: list[int],
) -> list[RegistroHistorico]:
    registros: list[RegistroHistorico] = []
    if df_mensual.empty:
        return [RegistroHistorico(estacion, nombre, variable, anio, mes_objetivo, None, "sin_datos", "climatología mensual vacía") for anio in anios]

    col_valor = buscar_columna(df_mensual, VARIABLES[variable]["mensual"])
    if col_valor is None:
        msg = f"no encuentro columna mensual para {variable}; columnas: {list(df_mensual.columns)}"
        return [RegistroHistorico(estacion, nombre, variable, anio, mes_objetivo, None, "sin_datos", msg) for anio in anios]

    datos_por_anio: dict[int, float] = {}
    for _, row in df_mensual.iterrows():
        anio, mes = extraer_anio_mes(row, df_mensual)
        if anio is None or mes is None:
            continue
        if anio >= anio_actual or mes != mes_objetivo or mes == 13:
            continue
        if anio not in anios:
            continue
        valor = a_float(row[col_valor])
        if not math.isnan(valor):
            datos_por_anio[anio] = float(valor)

    for anio in anios:
        if anio in datos_por_anio:
            registros.append(RegistroHistorico(
                estacion=estacion,
                nombre=nombre,
                variable=variable,
                anio=anio,
                mes=mes_objetivo,
                media_mensual=redondear(datos_por_anio[anio]),
                estado="ok",
                mensaje="ok",
            ))
        else:
            registros.append(RegistroHistorico(
                estacion=estacion,
                nombre=nombre,
                variable=variable,
                anio=anio,
                mes=mes_objetivo,
                media_mensual=None,
                estado="sin_datos",
                mensaje="no hay dato mensual para ese año/mes",
            ))

    return registros


def crear_grafico(
    salida_graficos: Path,
    estacion: str,
    nombre: str,
    variable: str,
    fecha_ref: date,
    serie_actual: pd.DataFrame,
    media_actual: float,
    media_historica: float | None,
    periodo_historico: str | None,
) -> str:
    var = VARIABLES[variable]
    unidad = var["unidad"]
    mes_nombre = MESES[fecha_ref.month]

    plt.figure(figsize=(9.0, 5.2))
    plt.plot(
        serie_actual["dia"],
        serie_actual["valor"],
        marker="o",
        linewidth=2,
        label=f"{fecha_ref.year}: evolución diaria",
    )

    plt.axhline(media_actual, linestyle="--", linewidth=1.8, label=f"Media actual: {media_actual:.1f} {unidad}")

    if media_historica is not None:
        plt.axhline(
            media_historica,
            linestyle=":",
            linewidth=2.3,
            label=f"Media años anteriores {periodo_historico}: {media_historica:.1f} {unidad}",
        )

    ultimo_dia = int(serie_actual["dia"].max())
    plt.title(
        f"{var['titulo_diario']} · {mes_nombre} {fecha_ref.year}\n"
        f"{nombre} ({estacion}) · hasta día {ultimo_dia}"
    )
    plt.xlabel("Día del mes")
    plt.ylabel(unidad)
    plt.xlim(1, monthrange(fecha_ref.year, fecha_ref.month)[1])
    plt.grid(True, alpha=0.25)
    plt.legend(loc="best", fontsize=8)
    plt.tight_layout()

    nombre_archivo = f"{estacion}_{variable}_{fecha_ref:%Y%m}_evolucion_historica.png"
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
    anios: list[int],
    salida_graficos: Path,
    cache_dir: Path,
    forzar_descarga: bool = False,
) -> tuple[ResultadoVariable, list[RegistroHistorico], pd.DataFrame]:
    var = VARIABLES[variable]
    anio_actual = fecha_ref.year
    mes = fecha_ref.month

    # 1) Datos diarios del año actual.
    df_diarios = descargar_diarios_mes(api_key, estacion, anio_actual, mes, dia_final=fecha_ref.day)
    serie_actual = preparar_serie_diaria(df_diarios, variable, anio_actual, mes)
    media_actual = float(serie_actual["valor"].mean())
    dias_actual = int(serie_actual["dia"].nunique())
    ultimo_dia_actual = serie_actual["fecha"].dt.date.max().isoformat()

    serie_export = serie_actual.copy()
    serie_export["estacion"] = estacion
    serie_export["nombre"] = nombre
    serie_export["variable"] = variable
    serie_export["anio"] = anio_actual
    serie_export["mes"] = mes
    serie_export["fecha_iso"] = serie_export["fecha"].dt.strftime("%Y-%m-%d")
    serie_export = serie_export[["estacion", "nombre", "variable", "anio", "mes", "fecha_iso", "dia", "valor"]]

    # 2) Climatologías mensuales/anuales de años anteriores.
    anio_ini = min(anios)
    anio_fin = max(anios)
    df_mensual = cargar_o_descargar_mensuales(
        api_key=api_key,
        estacion=estacion,
        anio_ini=anio_ini,
        anio_fin=anio_fin,
        cache_dir=cache_dir,
        forzar_descarga=forzar_descarga,
    )
    historico = preparar_historico_mensual(
        df_mensual=df_mensual,
        estacion=estacion,
        nombre=nombre,
        variable=variable,
        anio_actual=anio_actual,
        mes_objetivo=mes,
        anios=anios,
    )

    medias_validas = [h.media_mensual for h in historico if h.estado == "ok" and h.media_mensual is not None]
    media_historica = float(pd.Series(medias_validas).mean()) if medias_validas else None
    anios_usados = [h.anio for h in historico if h.estado == "ok" and h.media_mensual is not None]
    periodo_historico = f"{min(anios_usados)}-{max(anios_usados)}" if anios_usados else None
    diferencia = media_actual - media_historica if media_historica is not None else None

    grafico = crear_grafico(
        salida_graficos=salida_graficos,
        estacion=estacion,
        nombre=nombre,
        variable=variable,
        fecha_ref=fecha_ref,
        serie_actual=serie_actual,
        media_actual=media_actual,
        media_historica=media_historica,
        periodo_historico=periodo_historico,
    )

    estado = "ok" if media_historica is not None else "parcial"
    mensaje = "ok" if media_historica is not None else "hay datos actuales, pero no hay suficiente histórico mensual"

    resultado = ResultadoVariable(
        estacion=estacion,
        nombre=nombre,
        variable=variable,
        titulo=var["titulo_resumen"],
        unidad=var["unidad"],
        anio_actual=anio_actual,
        mes=mes,
        estado=estado,
        mensaje=mensaje,
        media_actual=redondear(media_actual),
        media_historica=redondear(media_historica),
        diferencia=redondear(diferencia),
        dias_actual=dias_actual,
        ultimo_dia_actual=ultimo_dia_actual,
        anios_historicos_usados=len(medias_validas),
        periodo_historico=periodo_historico,
        grafico=grafico,
    )
    return resultado, historico, serie_export


def guardar_csv_dicts(ruta: Path, filas: list[dict[str, Any]], campos: list[str]) -> Path:
    with ruta.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=campos)
        writer.writeheader()
        for fila in filas:
            writer.writerow({campo: fila.get(campo) for campo in campos})
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

    fecha_actualizacion = datetime.now(TZ_LOCAL).strftime("%d/%m/%Y %H:%M")
    mes_nombre = MESES[fecha_ref.month]

    css = """
    :root { color-scheme: light; }
    body { margin: 0; font-family: Arial, Helvetica, sans-serif; background: #f4f6f8; color: #1f2933; }
    .wrap { max-width: 1180px; margin: 0 auto; padding: 18px; }
    .cabecera { background: linear-gradient(135deg, #263544, #516575); color: white; border-radius: 18px; padding: 20px; margin-bottom: 18px; box-shadow: 0 8px 24px rgba(0,0,0,.12); }
    .cabecera h1 { margin: 0 0 8px 0; font-size: 26px; }
    .cabecera p { margin: 4px 0; opacity: .95; line-height: 1.45; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(360px, 1fr)); gap: 16px; }
    .estacion { background: white; border-radius: 18px; padding: 16px; box-shadow: 0 6px 18px rgba(0,0,0,.08); }
    .estacion h2 { margin: 0 0 12px 0; font-size: 20px; border-bottom: 1px solid #e5e7eb; padding-bottom: 8px; }
    .codigo { color: #64748b; font-size: 13px; font-weight: normal; }
    .variable { border: 1px solid #e5e7eb; border-radius: 14px; margin: 12px 0; padding: 12px; background: #fbfdff; }
    .variable h3 { margin: 0 0 8px 0; font-size: 16px; }
    .datos { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-bottom: 10px; font-size: 13px; }
    .dato { background: #eef2f6; border-radius: 10px; padding: 8px; text-align: center; }
    .dato strong { display: block; font-size: 17px; margin-bottom: 2px; color: #172033; }
    img { width: 100%; height: auto; border-radius: 12px; border: 1px solid #e5e7eb; background: white; }
    .aviso { color: #9a3412; background: #fff7ed; border: 1px solid #fed7aa; border-radius: 12px; padding: 10px; font-size: 13px; }
    .pie { margin: 18px 0 0 0; color: #64748b; font-size: 13px; text-align: center; line-height: 1.45; }
    @media (max-width: 640px) { .datos { grid-template-columns: 1fr; } .cabecera h1 { font-size: 22px; } }
    """

    partes: list[str] = []
    partes.append("<!doctype html>")
    partes.append('<html lang="es">')
    partes.append("<head>")
    partes.append('<meta charset="utf-8">')
    partes.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    partes.append(f"<title>AEMET · Evolución diaria vs media histórica · {html.escape(mes_nombre)} {fecha_ref.year}</title>")
    partes.append(f"<style>{css}</style>")
    partes.append("</head>")
    partes.append("<body>")
    partes.append('<div class="wrap">')
    partes.append('<section class="cabecera">')
    partes.append("<h1>AEMET · Evolución diaria frente a media mensual histórica</h1>")
    partes.append(f"<p><strong>Periodo actual:</strong> {html.escape(mes_nombre)} {fecha_ref.year}, hasta el último día disponible en AEMET.</p>")
    partes.append("<p><strong>Comparación:</strong> media mensual del mismo mes calculada con años anteriores al año actual.</p>")
    partes.append(f"<p><strong>Variables:</strong> {', '.join(html.escape(VARIABLES[v]['titulo_resumen']) for v in variables)}</p>")
    partes.append(f"<p><strong>Actualización del visor:</strong> {html.escape(fecha_actualizacion)}</p>")
    partes.append("</section>")
    partes.append('<main class="grid">')

    for estacion, nombre in estaciones:
        partes.append('<section class="estacion">')
        partes.append(f"<h2>{html.escape(nombre)} <span class='codigo'>{html.escape(estacion)}</span></h2>")
        resultados_estacion = {r.variable: r for r in por_estacion.get(estacion, [])}
        for variable in variables:
            r = resultados_estacion.get(variable)
            partes.append('<article class="variable">')
            partes.append(f"<h3>{html.escape(VARIABLES[variable]['titulo_resumen'])}</h3>")
            if r and r.estado in {"ok", "parcial"}:
                unidad = html.escape(r.unidad)
                actual_txt = f"{r.media_actual:.1f}" if r.media_actual is not None else "—"
                hist_txt = f"{r.media_historica:.1f}" if r.media_historica is not None else "—"
                dif_txt = f"{r.diferencia:+.1f}" if r.diferencia is not None else "—"
                partes.append('<div class="datos">')
                partes.append(f"<div class='dato'><strong>{actual_txt}</strong>Media actual {unidad}</div>")
                partes.append(f"<div class='dato'><strong>{hist_txt}</strong>Media histórica {unidad}</div>")
                partes.append(f"<div class='dato'><strong>{dif_txt}</strong>Diferencia {unidad}</div>")
                partes.append("</div>")
                if r.grafico:
                    partes.append(f"<img src='{html.escape(r.grafico)}' alt='{html.escape(r.titulo)} en {html.escape(nombre)}'>")
                partes.append(
                    f"<p class='codigo'>Último día actual: {html.escape(r.ultimo_dia_actual or '')} · "
                    f"Días actuales usados: {r.dias_actual or 0} · "
                    f"Años históricos usados: {r.anios_historicos_usados or 0} ({html.escape(r.periodo_historico or '')})</p>"
                )
                if r.estado == "parcial":
                    partes.append(f"<div class='aviso'>{html.escape(r.mensaje)}</div>")
            else:
                mensaje = r.mensaje if r else "sin resultado"
                partes.append(f"<div class='aviso'>Sin datos suficientes. {html.escape(mensaje)}</div>")
            partes.append("</article>")
        partes.append("</section>")

    partes.append("</main>")
    partes.append(
        '<p class="pie">Fuente: AEMET OpenData. Elaboración automática. '
        'La media histórica mostrada es una media propia calculada con climatologías mensuales/anuales de años anteriores; no es una normal climática oficial.</p>'
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


def calcular_anios_historicos(fecha_ref: date, anios_historico: int, desde_anio: int | None) -> list[int]:
    if desde_anio is not None:
        anios = list(range(desde_anio, fecha_ref.year))
    else:
        anio_ini = max(1900, fecha_ref.year - anios_historico)
        anios = list(range(anio_ini, fecha_ref.year))
    if not anios:
        raise AemetError("No hay años históricos anteriores al año actual")
    return anios


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Genera gráficos AEMET: evolución diaria actual frente a media mensual histórica propia."
    )
    parser.add_argument("--fecha", default=None, help="Fecha de referencia YYYY-MM-DD. Por defecto, hoy en hora peninsular.")
    parser.add_argument("--api-key", default=None, help="API key de AEMET. También puede usarse AEMET_API_KEY.")
    parser.add_argument("--salida", default="docs/clima", help="Carpeta de salida web. Por defecto: docs/clima")
    parser.add_argument("--estaciones", default=None, help="Códigos separados por coma. Ejemplo: 8416,8293X")
    parser.add_argument("--variables", default="tmed", help="Variables separadas por coma. Por defecto: tmed. Opciones: tmed,tmax,tmin,hr")
    parser.add_argument("--anios-historico", type=int, default=10, help="Número de años anteriores a usar si no se indica --desde-anio. Por defecto: 10")
    parser.add_argument("--desde-anio", type=int, default=None, help="Primer año histórico a usar. Ejemplo: 2016")
    parser.add_argument("--forzar-descarga", action="store_true", help="Ignora la caché histórica y vuelve a descargar climatologías mensuales/anuales")
    args = parser.parse_args()

    try:
        api_key = api_key_desde_entorno(args.api_key)
        fecha_ref = parse_fecha(args.fecha)
        estaciones = seleccionar_estaciones(args.estaciones)
        variables = seleccionar_variables(args.variables)
        anios = calcular_anios_historicos(fecha_ref, args.anios_historico, args.desde_anio)

        salida = Path(args.salida)
        salida_graficos = salida / "graficos"
        salida_datos = salida / "datos"
        cache_dir = salida_datos / "cache"
        salida_graficos.mkdir(parents=True, exist_ok=True)
        salida_datos.mkdir(parents=True, exist_ok=True)
        cache_dir.mkdir(parents=True, exist_ok=True)

        resultados: list[ResultadoVariable] = []
        registros_historicos: list[RegistroHistorico] = []
        diarios_actuales: list[pd.DataFrame] = []

        total = len(estaciones) * len(variables)
        contador = 0
        print(
            f"Procesando {len(estaciones)} estaciones y {len(variables)} variables ({total} gráficos). "
            f"Mes actual: {fecha_ref:%Y-%m}. Histórico: {min(anios)}-{max(anios)}."
        )

        for estacion, nombre in estaciones:
            print(f"\n{estacion} - {nombre}")
            for variable in variables:
                contador += 1
                print(f"  [{contador}/{total}] {variable}...", end=" ", flush=True)
                try:
                    resultado, historico, diario_actual = procesar_estacion_variable(
                        api_key=api_key,
                        estacion=estacion,
                        nombre=nombre,
                        variable=variable,
                        fecha_ref=fecha_ref,
                        anios=anios,
                        salida_graficos=salida_graficos,
                        cache_dir=cache_dir,
                        forzar_descarga=args.forzar_descarga,
                    )
                    resultados.append(resultado)
                    registros_historicos.extend(historico)
                    diarios_actuales.append(diario_actual)
                    print(resultado.estado)
                except Exception as exc:  # noqa: BLE001
                    resultados.append(ResultadoVariable(
                        estacion=estacion,
                        nombre=nombre,
                        variable=variable,
                        titulo=VARIABLES[variable]["titulo_resumen"],
                        unidad=VARIABLES[variable]["unidad"],
                        anio_actual=fecha_ref.year,
                        mes=fecha_ref.month,
                        estado="sin_datos",
                        mensaje=str(exc),
                    ))
                    print("sin_datos")
                time.sleep(0.9)

        resumen_path = guardar_csv_dicts(
            salida_datos / "resumen.csv",
            [asdict(r) for r in resultados],
            [
                "estacion", "nombre", "variable", "titulo", "unidad", "anio_actual", "mes",
                "estado", "mensaje", "media_actual", "media_historica", "diferencia",
                "dias_actual", "ultimo_dia_actual", "anios_historicos_usados",
                "periodo_historico", "grafico",
            ],
        )
        historico_path = guardar_csv_dicts(
            salida_datos / "historico_mensual.csv",
            [asdict(h) for h in registros_historicos],
            ["estacion", "nombre", "variable", "anio", "mes", "media_mensual", "estado", "mensaje"],
        )

        if diarios_actuales:
            diarios_df = pd.concat(diarios_actuales, ignore_index=True)
        else:
            diarios_df = pd.DataFrame(columns=["estacion", "nombre", "variable", "anio", "mes", "fecha_iso", "dia", "valor"])
        diarios_path = salida_datos / "diarios_actual.csv"
        diarios_df.to_csv(diarios_path, index=False, encoding="utf-8-sig")

        json_path = salida_datos / "aemet_clima.json"
        json_path.write_text(
            json.dumps(
                {
                    "actualizado": datetime.now(TZ_LOCAL).isoformat(),
                    "fecha_referencia": fecha_ref.isoformat(),
                    "fuente": "AEMET OpenData",
                    "nota": "Media histórica propia calculada con climatologías mensuales/anuales de años anteriores; no es normal climática oficial.",
                    "resultados": [asdict(r) for r in resultados],
                    "historico_mensual": [asdict(h) for h in registros_historicos],
                    "diarios_actuales": diarios_df.to_dict(orient="records"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        html_path = generar_index_html(
            resultados=resultados,
            salida=salida,
            fecha_ref=fecha_ref,
            estaciones=estaciones,
            variables=variables,
        )

        ok = sum(1 for r in resultados if r.estado == "ok")
        parciales = sum(1 for r in resultados if r.estado == "parcial")
        sin_datos = sum(1 for r in resultados if r.estado == "sin_datos")

        print("\nResultado")
        print("---------")
        print(f"OK: {ok}")
        print(f"Parcial: {parciales}")
        print(f"Sin datos: {sin_datos}")
        print(f"HTML: {html_path}")
        print(f"CSV resumen: {resumen_path}")
        print(f"CSV histórico: {historico_path}")
        print(f"CSV diarios: {diarios_path}")
        print(f"JSON: {json_path}")
        return 0

    except AemetError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except requests.RequestException as exc:
        print(f"ERROR de red: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
