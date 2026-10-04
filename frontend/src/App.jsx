import { useEffect, useRef, useState } from 'react'
import mapboxgl from 'mapbox-gl'
import accessibleToiletIcon from '../../images/Accessible_Toilet.png'
import stairsMarkerIcon from '../../images/stairs_marker.svg'
import 'mapbox-gl/dist/mapbox-gl.css'
import './App.css'

const HONG_KONG_BOUNDS = {
  minLng: 113.8,
  maxLng: 114.5,
  minLat: 22.1,
  maxLat: 22.6,
}
const BACKEND_URL = import.meta.env.VITE_BACKEND_URL || '/api'

mapboxgl.accessToken = import.meta.env.VITE_MAPBOX_TOKEN || ''

async function fetchWithTimeout(url, options, timeoutMs, serviceName) {
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs)

  try {
    return await fetch(url, { ...options, signal: controller.signal })
  } catch (error) {
    if (error.name === 'AbortError') {
      throw new Error(`${serviceName}逾時，請稍後再試。`)
    }
    throw error
  } finally {
    clearTimeout(timeoutId)
  }
}

async function readJsonResponse(response, serviceName) {
  const body = await response.text()
  let payload

  try {
    payload = JSON.parse(body)
  } catch {
    if (!response.ok) {
      throw new Error(`${serviceName}目前無法使用（HTTP ${response.status}），請確認服務已啟動。`)
    }
    throw new Error(`${serviceName}回傳空白或無效資料，請稍後再試。`)
  }

  if (!response.ok) {
    throw new Error(payload.error || payload.message || `${serviceName}請求失敗（HTTP ${response.status}）。`)
  }

  return payload
}

function validateHongKongCoordinate(coordinates, label) {
  const [lng, lat] = coordinates
  if (
    lng < HONG_KONG_BOUNDS.minLng ||
    lng > HONG_KONG_BOUNDS.maxLng ||
    lat < HONG_KONG_BOUNDS.minLat ||
    lat > HONG_KONG_BOUNDS.maxLat
  ) {
    throw new Error(`${label}搜尋結果不在香港範圍內，請輸入香港的地點名稱。`)
  }

  return coordinates
}

async function geocodeLocation(query, label) {
  if (!query.trim()) {
    throw new Error(`請輸入${label}地點。`)
  }

  const params = new URLSearchParams({
    q: query.trim(),
    lat: '22.32',
    lon: '114.17',
    limit: '8',
    lang: 'en',
  })
  const url = `https://photon.komoot.io/api/?${params}`
  let response
  try {
    response = await fetchWithTimeout(url, undefined, 20000, '地點搜尋')
  } catch (error) {
    if (error instanceof Error && error.message.includes('逾時')) {
      throw error
    }
    throw new Error('無法連線到地點搜尋服務，請檢查網路連線。')
  }
  const payload = await readJsonResponse(response, '地點搜尋服務')

  const feature = payload.features?.find((candidate) => {
    const coordinates = candidate.geometry?.coordinates
    return Array.isArray(coordinates) && coordinates.length === 2 &&
      coordinates[0] >= HONG_KONG_BOUNDS.minLng &&
      coordinates[0] <= HONG_KONG_BOUNDS.maxLng &&
      coordinates[1] >= HONG_KONG_BOUNDS.minLat &&
      coordinates[1] <= HONG_KONG_BOUNDS.maxLat
  })
  if (!feature) {
    throw new Error(`找不到${label}「${query.trim()}」，請嘗試輸入更完整的地點名稱。`)
  }

  const coordinates = feature.geometry.coordinates
  return {
    coordinates: validateHongKongCoordinate(coordinates, label),
    name: feature.properties?.name || query.trim(),
  }
}

function getSlopeColor(value) {
  if (value == null) return '#94a3b8'
  const abs = Math.abs(value)
  if (abs < 3) return '#22c55e'
  if (abs < 6) return '#facc15'
  if (abs < 10) return '#f97316'
  return '#ef4444'
}

function getFacilityColor(type = '') {
  const text = String(type).toLowerCase()
  if (text.includes('step') || text.includes('stair')) return '#ef4444'
  if (text.includes('elevator') || text.includes('lift')) return '#16a34a'
  if (text.includes('ramp')) return '#f59e0b'
  if (text.includes('toilet')) return '#2563eb'
  if (text.includes('obstacle')) return '#7c3aed'
  return '#64748b'
}

function getFacilityLabel(type = '') {
  const text = String(type).toLowerCase()
  if (text.includes('step') || text.includes('stair')) return '樓梯'
  if (text.includes('elevator') || text.includes('lift')) return '升降機'
  if (text.includes('ramp')) return '斜坡'
  if (text.includes('toilet')) return '無障礙廁所'
  if (text.includes('obstacle')) return '障礙'
  return '設施'
}

function App() {
  const mapContainerRef = useRef(null)
  const mapRef = useRef(null)
  const markersRef = useRef([])
  const [startInput, setStartInput] = useState('')
  const [endInput, setEndInput] = useState('')
  const [routeData, setRouteData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const mapError = mapboxgl.accessToken
    ? ''
    : '地圖尚未載入：請將 Frontend/.env.example 複製為 .env，並設定 VITE_MAPBOX_TOKEN。'

  useEffect(() => {
    if (!mapContainerRef.current || mapRef.current) return

    if (!mapboxgl.accessToken) {
      return
    }

    const map = new mapboxgl.Map({
      container: mapContainerRef.current,
      style: 'mapbox://styles/aaron504123/cmutmz3vh00hp01sd40m61umx',
      center: [114.17, 22.32],
      zoom: 13,
      attributionControl: false,
    })

    mapRef.current = map

    return () => {
      map.remove()
      mapRef.current = null
    }
  }, [])

  useEffect(() => {
    if (!routeData || !mapRef.current) return

    const map = mapRef.current
    const sourceId = 'accessible-route'
    const layerId = 'accessible-route-layer'

    if (map.getLayer(layerId)) map.removeLayer(layerId)
    if (map.getSource(sourceId)) map.removeSource(sourceId)

    markersRef.current.forEach((marker) => marker.remove())
    markersRef.current = []

    const routeFeatures = routeData.segments.map((segment, index) => ({
      type: 'Feature',
      properties: {
        slope: segment.slope_deg ?? null,
        color: getSlopeColor(segment.slope_deg),
        index,
      },
      geometry: {
        type: 'LineString',
        coordinates: [segment.from, segment.to],
      },
    }))

    map.addSource(sourceId, {
      type: 'geojson',
      data: {
        type: 'FeatureCollection',
        features: routeFeatures,
      },
    })

    map.addLayer({
      id: layerId,
      type: 'line',
      source: sourceId,
      layout: {
        'line-cap': 'round',
        'line-join': 'round',
        'line-simplification': 0.2,
      },
      paint: {
        'line-color': ['case', ['==', ['get', 'slope'], null], '#94a3b8', ['get', 'color']],
        'line-width': 7,
        'line-opacity': 0.95,
      },
    })

    const bounds = new mapboxgl.LngLatBounds()
    routeData.route.coordinates.forEach(([lng, lat]) => {
      bounds.extend([lng, lat])
    })
    map.fitBounds(bounds, { padding: 40, maxZoom: 16 })

    const startMarker = new mapboxgl.Marker({ color: '#16a34a' })
      .setLngLat(routeData.route.coordinates[0])
      .addTo(map)

    const endMarker = new mapboxgl.Marker({ color: '#dc2626' })
      .setLngLat(routeData.route.coordinates[routeData.route.coordinates.length - 1])
      .addTo(map)

    markersRef.current = [startMarker, endMarker]

    const obstacleFeatures = (routeData.facilities || [])
      .filter((facility) => /step|stair|elevator|lift|ramp|obstacle/i.test(facility.type || ''))
      .map((facility) => ({
        type: 'Feature',
        properties: {
          type: facility.type,
          name: facility.name || facility.type,
          color: getFacilityColor(facility.type),
        },
        geometry: {
          type: 'Point',
          coordinates: [facility.longitude, facility.latitude],
        },
      }))

    if (obstacleFeatures.length > 0) {
      const obstacleSourceId = 'route-obstacle-points'
      const obstacleLayerId = 'route-obstacle-points-layer'

      if (map.getLayer(obstacleLayerId)) map.removeLayer(obstacleLayerId)
      if (map.getSource(obstacleSourceId)) map.removeSource(obstacleSourceId)

      map.addSource(obstacleSourceId, {
        type: 'geojson',
        data: {
          type: 'FeatureCollection',
          features: obstacleFeatures,
        },
      })

      map.addLayer({
        id: obstacleLayerId,
        type: 'circle',
        source: obstacleSourceId,
        paint: {
          'circle-radius': 9,
          'circle-color': ['get', 'color'],
          'circle-stroke-color': '#ffffff',
          'circle-stroke-width': 2,
          'circle-opacity': 0.9,
        },
      })
    }

    routeData.facilities?.forEach((facility) => {
      const typeText = String(facility.type || '').toLowerCase()
      const isToilet = typeText.includes('toilet')
      const isStairs = typeText.includes('step') || typeText.includes('stair')
      const obstacleLabel = getFacilityLabel(facility.type)
      const obstacleColor = getFacilityColor(facility.type)
      let markerOptions = { color: obstacleColor }

      if (isToilet) {
        const icon = document.createElement('img')
        icon.src = accessibleToiletIcon
        icon.alt = facility.name || '無障礙廁所'
        icon.className = 'toilet-marker-icon'
        markerOptions = { element: icon, anchor: 'bottom' }
      } else if (isStairs) {
        const icon = document.createElement('img')
        icon.src = stairsMarkerIcon
        icon.alt = facility.name || '樓梯'
        icon.className = 'stairs-marker-icon'
        markerOptions = { element: icon, anchor: 'bottom' }
      }

      const marker = new mapboxgl.Marker(markerOptions)
        .setLngLat([facility.longitude, facility.latitude])
        .setPopup(
          new mapboxgl.Popup({ offset: 18 }).setHTML(`
            <strong>${facility.name || obstacleLabel}</strong><br />
            ${facility.type || obstacleLabel}
          `),
        )
        .addTo(map)

      markersRef.current.push(marker)
    })
  }, [routeData])

  const handleRouteRequest = async () => {
    setLoading(true)
    setError('')
    setStatus('')

    try {
      const [startLocation, endLocation] = await Promise.all([
        geocodeLocation(startInput, '起點'),
        geocodeLocation(endInput, '終點'),
      ])

      const response = await fetchWithTimeout(`${BACKEND_URL}/route`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          start: startLocation.coordinates,
          end: endLocation.coordinates,
        }),
      }, 60000, '路線計算')

      const payload = await readJsonResponse(response, '後端路線服務')

      setRouteData(payload)
      const obstacleSummary = payload.route_obstacles || {}
      const obstacleText = obstacleSummary.has_step_obstacle
        ? '有樓梯障礙'
        : obstacleSummary.has_elevator_access
          ? '有升降機接近'
          : '未檢測到明顯樓梯／升降機'
      setStatus(`起點：${startLocation.name}；終點：${endLocation.name}。${obstacleText}，找到 ${payload.facilities?.length ?? 0} 個設施資訊。`)
    } catch (requestError) {
      setError(
        requestError instanceof TypeError
          ? '無法連線到後端。請確認 Flask 正在 localhost:5000 執行。'
          : requestError.message,
      )
      setStatus('')
    } finally {
      setLoading(false)
    }
  }

  const maxSlope = routeData?.summary?.max_slope_deg ?? 0
  const facilityCount = routeData?.facilities?.length ?? 0

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-block">
          <span className="eyebrow">Accessible Route</span>
          <h1>無障礙路線導航</h1>
        </div>

        <div className="control-group">
          <label>
            起點地點
            <input
              value={startInput}
              onChange={(event) => setStartInput(event.target.value)}
              placeholder="例如：香港站、中環碼頭"
            />
          </label>

          <label>
            終點地點
            <input
              value={endInput}
              onChange={(event) => setEndInput(event.target.value)}
              placeholder="例如：香港大學、金鐘站"
            />
          </label>

          <button type="button" onClick={handleRouteRequest} disabled={loading}>
            {loading ? '計算中...' : '計算路線'}
          </button>
        </div>

        <div className="summary-grid">
          <div className="stat-card">
            <span>最大坡度</span>
            <strong>{maxSlope ? `${maxSlope.toFixed(1)}°` : '--'}</strong>
          </div>
          <div className="stat-card">
            <span>設施數量</span>
            <strong>{facilityCount}</strong>
          </div>
        </div>

        <div className="obstacle-panel">
          <h2>路線障礙狀態</h2>
          <div className="obstacle-badges">
            <span className={`obstacle-badge ${routeData?.route_obstacles?.has_step_obstacle ? 'step' : 'safe'}`}>
              {routeData?.route_obstacles?.has_step_obstacle ? '有樓梯' : '無明顯樓梯'}
            </span>
            <span className={`obstacle-badge ${routeData?.route_obstacles?.has_elevator_access ? 'elevator' : 'safe'}`}>
              {routeData?.route_obstacles?.has_elevator_access ? '有升降機' : '未確認升降機'}
            </span>
          </div>
          {routeData?.warning ? <div className="alert info route-warning">{routeData.warning}</div> : null}
        </div>

        <div className="legend">
          <h2>坡度顏色</h2>
          <div className="legend-row"><span className="dot green" /> 輕度坡度</div>
          <div className="legend-row"><span className="dot yellow" /> 中度坡度</div>
          <div className="legend-row"><span className="dot orange" /> 較陡坡度</div>
          <div className="legend-row"><span className="dot red" /> 極陡坡度</div>
        </div>

        {error ? <div className="alert error">{error}</div> : null}
        {status ? <div className="alert info">{status}</div> : null}
        {mapError ? <div className="alert error">{mapError}</div> : null}
      </aside>

      <main className="map-area">
        <div ref={mapContainerRef} className="map-container" />
      </main>
    </div>
  )
}

export default App
