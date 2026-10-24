# Plan de implementación · propuesta de tablero v4

Fuente del diseño:
- Lienzo «Venko dashboard: responsive proposal» (https://claude.ai/artifact/4mRcYvpMkVgoR52bN1zCm1): versión 4, escritorio, tableta y celular de Resumen, Prueba (HMI), Alarmas, Históricos, Reportes y Sistema, con la nota «Parámetros nuevos que la configuración actual no contempla».
- Prototipo navegable `docs/propuesta-tablero/Venko_AKVO_SEMCA_propuesta.html` (también en https://claude.ai/artifact/AQVJXLQgGNc63sogLYHmzG): las seis ventanas funcionando con datos de ejemplo, tema claro y oscuro.

El prototipo ya tiene el HTML, el CSS (tokens claro/oscuro, *container queries*) y el dibujo de los medidores en SVG que se pueden trasladar casi tal cual a `web/`.

## Punto de partida (lo que ya existe)

| Pieza | Dónde | Sirve tal cual para |
|---|---|---|
| Vistas `overview`, `test` (HMI), `alarms`, `history`, `reports` con ruteo por `#` | `web/js/main.js`, `web/js/modules/*.js` | Mismas cinco ventanas; falta **Sistema** |
| Modo oscuro (botón «Fondo») y tema de Highcharts | `web/js/modules/theme.js`, `web/css/dashboard.css` | Se reutiliza; cambian los colores |
| `warn_margin` (10 % del rango) | `config/sites/<sitio>.json` → `overview.js` | Advertencia en Resumen; hay que llevarla al backend |
| Episodios de alarma con inicio y fin | `backend/api/data.py` (`collapse_episodes`) | Tabla de historial de Alarmas |
| Pruebas con nombre, operador y notas; comentarios con sensor | `backend/api/testruns.py` | Prueba (HMI) sin cambios de API |
| Mín/prom/máx semanal, reportes HTML/Excel | `backend/common/report_builder.py` | Reportes; faltan disponibilidad y recuperación |
| Exportar lecturas Excel/CSV | `GET /export` | Históricos |
| Telemetría: CPU, RAM, disco, IP, SO, hostname, modelo, serie | gateway `get_system_status()` + `hwinfo.py` | Sistema (parte) |
| API simulada para trabajar la UI | `tools/mock_api.py` | Desarrollo de todas las fases de UI |

## Decisiones antes de empezar

1. **Sitio objetivo.** La propuesta usa los 10 sensores SEMCA (`SEMCA_OSM_devices.csv`). Hoy esos sensores están en **cmd4** (Venko-Keken, simulado); **akvo01005** tiene los 5 sensores de Osmosis. Elegir en qué sitio se valida primero (sugerido: cmd4, porque ya tiene los 10 sensores y no afecta a un cliente).
2. **Aprobación del cliente** del diseño (prototipo) antes de la fase 1.
3. **Límites de los indicadores de la ósmosis** (recuperación, ΔP filtro, ΔP membranas, balance): valores mín/máx que dará el cliente o el proceso.
4. **Umbrales de recursos del gateway**: propuesta 80 % advertencia / 90 % alarma para CPU, RAM y disco.
5. **Medidores**: sustituir los `solidgauge` de Highcharts por el dial SVG del prototipo (recomendado: más ligero, igual en tableta/celular como barra) o conservar Highcharts con bandas de color.

## Fase 1 · Nuevo aspecto y adaptación a pantallas (solo `web/`) · tamaño M

Sin cambios de backend ni de gateway; se puede publicar en los tres sitios en cuanto esté.

1. **Tokens y tipografía** — `web/css/dashboard.css`: reemplazar la paleta actual por los tokens del prototipo (claro = v4, oscuro = v3 GHOTRIX), Montserrat, fondo de olas con velo (`--veil`), marcos de estado gris / amarillo `#F2B705` / rojo `#D32F2F`.
2. **Encabezado** — `web/index.html`: banda azul noche con marca, cliente, estado en línea, «Prueba en curso / Monitoreo libre», botón de tema y pestañas horizontales (sustituye al menú lateral; en celular las pestañas se desplazan de lado).
3. **Rejillas fluidas** — quitar `repeat(N, 320px)` de `overview.js` (línea de `.gauges`) y usar `auto-fill, minmax(200px, 1fr)`; *container queries* a 900 px (dial → barra) y 560 px (tablas → tarjetas, 2 columnas de sensores).
4. **Medidores** — `overview.js`: tarjeta de sensor con estado en texto (Normal / Advertencia / Alarma), borde de color, dial SVG con zonas y barra en pantallas angostas (portar `sensorCard()` del prototipo).
5. **Vistas** — ajustar el marcado de `hmi.js`, `alarms.js`, `history.js`, `reports.js` a las tarjetas del prototipo; tablas con versión en tarjetas para celular.
6. **Históricos: selector de sensores desplegable** — cambiar `sensorPicker()` de `web/js/modules/trend.js` por el botón «Sensores (n) · P3, P4…» con casillas agrupadas (Presión / Flujo / Ambiente) y Todos / Ninguno / Listo; agrupar las gráficas por unidad.
7. **Líneas de advertencia y alarma** en las gráficas de HMI e Históricos (`plotLines` de Highcharts con `min/max` y `warn_margin`).
8. **Pruebas**: revisar en escritorio 1440, tableta 834 y celular 390, claro y oscuro, con `tools/mock_api.py`.

Entrega: `tools/deploy_web.sh` a **cmd4, demo y akvo01005** (un cambio en `web/` va a todos los sitios).

## Fase 2 · Ventana Sistema (web + gateway) · tamaño M

### 2a · Con los datos actuales (solo web)
- Nueva vista `sistema` (`web/js/modules/system.js`, pestaña en `index.html`, ruta en `main.js`). Mover ahí la tabla «Sistema» de Resumen (`updateSystem()` en `overview.js`) y dejar en Resumen solo un resumen con enlace.
- Gateway y Equipo: cliente, gateway, estado, última lectura, hora local, muestreo, modelo, N.º de serie, hostname, IP, SO.
- Recursos CPU / RAM / disco: número + marco amarillo/rojo según umbrales (decisión 4), mín/máx de 24 h con una consulta nueva a `system_raw` (`backend/common/queries.py` + ruta `GET /system/stats`).
- Umbrales en `config/sites/<sitio>.json`, sección nueva `system_limits` (`{"cpu": [80, 90], "ram": [80, 90], "disk": [80, 90]}`), validada en `tools/sync_config.py`.

### 2b · Telemetría nueva del gateway (Akvo_Green)
En `gateway/edge_node_improved.py` → `get_system_status()` (y pruebas en `tests/test_edge_node/`):

| Campo nuevo | Cálculo |
|---|---|
| `uptime_s` | `time.time() − psutil.boot_time()` |
| `wifi_ssid`, `wifi_profile`, `wifi_signal` | `nmcli -t -f ACTIVE,SSID,SIGNAL,NAME dev wifi` / conexión activa (akvo-wifi1/2) |
| `modbus` | `{connected, port, baudrate, simulated, read_errors, sensors_ok}` (hoy solo en el archivo de estado local) |
| `mqtt_since` | desde cuándo está conectado (hoy en `mqtt.status()`) |
| `oled_enabled` | `display.enabled` del config |

Desplegar con `tools/push_site.sh` en cada Pi. El dashboard muestra «—» mientras un Pi no tenga el código nuevo.

## Fase 3 · Indicadores de la ósmosis · tamaño M

1. **Configuración** — sección nueva `derived` en `config/sites/<sitio>.json` (no se ejecuta código: solo operaciones fijas):
   ```json
   "derived": [
     {"key": "REC",  "label": "Recuperación",      "unit": "%",   "op": "ratio", "a": "DEV_7.Fj2_flujo_de_permeado", "b": "DEV_6.Fj1_flujo_de_alimentacion", "scale": 100, "min": 0, "max": 100, "decimals": 0},
     {"key": "DPF",  "label": "ΔP del filtro",     "unit": "psi", "op": "diff",  "a": "DEV_1.P1_pre_filtro", "b": "DEV_2.P2_pos_filtro", "min": 0, "max": 15},
     {"key": "DPM",  "label": "ΔP de membranas",   "unit": "psi", "op": "diff",  "a": "DEV_3.P3_pre_membranas", "b": "DEV_4.P4_pos_membranas", "min": 0, "max": 40},
     {"key": "BAL",  "label": "Balance de flujos", "unit": "gpm", "op": "balance", "a": "DEV_6.Fj1_flujo_de_alimentacion", "minus": ["DEV_7.Fj2_flujo_de_permeado", "DEV_8.Fj3_flujo_de_rechazo"], "min": -1, "max": 1}
   ]
   ```
   Los límites del ejemplo son de relleno: usar los de la decisión 3. `tools/sync_config.py` valida que las claves existan y que `op` sea `ratio | diff | balance`.
2. **Resumen** — tarjeta «Indicadores de la ósmosis» calculada en el navegador con `/live`; con estado y marco de color como los sensores. Se oculta si el sitio no tiene `derived`.
3. **Reportes** — `report_builder.py`: recuperación promedio de la semana = `Σ Fj2 / Σ Fj1 × 100` (no el promedio de porcentajes) y mín/prom/máx de cada indicador.
4. **Históricos (opcional)** — graficar indicadores como series calculadas a partir de los mismos *buckets*.

## Fase 4 · Advertencias como eventos y resúmenes · tamaño M–L

1. **Advertencia en el backend** — `backend/common/queries.py` `fetch_abnormal`: además de `LOW/HIGH/BUS_ERROR/EXCEPTION`, marcar `WARN_HIGH / WARN_LOW` cuando `v > máx − m·(máx − mín)` o `v < mín + m·(máx − mín)`, con `m = warn_margin` de cada sensor. Los umbrales salen del config como números (no texto del usuario), así que se pueden poner en la consulta sin romper la protección contra inyección (`parse_sensor_keys`).
2. **Resumen de alarmas** — `GET /alarms/summary?from&to`: totales por tipo (alarmas, advertencias, errores de lectura), sensor con más eventos y eventos por día (gráfica de barras de Alarmas). Botón Excel de Alarmas con el mismo patrón que `/export`.
3. **Resumen del rango en Históricos** — `GET /history/stats?from&to&sensors`: mín, prom, máx, último y lecturas en advertencia / alarma por sensor (Athena `min/avg/max/max_by/count_if`).
4. **Disponibilidad de datos** en Reportes: `lecturas OK / (segundos del periodo ÷ poll_interval × n.º de sensores) × 100`.
5. **Pruebas** — casos nuevos en `backend/tests/` (consultas generadas, umbrales, rutas nuevas en `test_router.py`), `tools/mock_api.py` con los campos nuevos.

## Fase 5 · Cierre · tamaño S

- `README.md` (guía del operador) y `CLAUDE.md`: vistas nuevas, secciones `derived` y `system_limits`, campos nuevos de telemetría.
- `docs/new-site-manual.md` (+ .docx): cómo definir indicadores y umbrales para un sitio nuevo.
- Desplegar backend (`sam build && sam deploy --config-env <sitio>`) y web en cmd4, demo y akvo01005; actualizar los Pi con `push_site.sh`.

## Orden sugerido y dependencias

```
Fase 1 (web) ──► entrega a los 3 sitios
   │
   ├─► Fase 2a (Sistema, web + /system/stats)
   │      └─► Fase 2b (gateway: uptime, WiFi, Modbus, OLED) ──► push_site a cada Pi
   ├─► Fase 3 (indicadores: config → Resumen → Reportes)
   └─► Fase 4 (advertencias como eventos, resúmenes de Alarmas e Históricos)
            └─► Fase 5 (documentación y despliegue final)
```

Las fases 2, 3 y 4 son independientes entre sí después de la fase 1 y se pueden hacer en cualquier orden.

## Riesgos

- **Sensores distintos por sitio**: los indicadores dependen de claves de sensor de cada sitio; sin `derived` la tarjeta se oculta, nunca se calcula con sensores equivocados.
- **Costo de Athena**: los resúmenes nuevos escanean el mismo rango que Históricos; respetar `MAX_RANGE_DAYS = 31` y, si hace falta, cachear `/alarms/summary` unos minutos.
- **Pi sin actualizar**: los campos nuevos de telemetría deben ser opcionales en el dashboard.
- **Tarjeta SD de demo al 91 %**: no aumentar lo que `push_site.sh` copia (excluir `docs/` y archivos CAD).
