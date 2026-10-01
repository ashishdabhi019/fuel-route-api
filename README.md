# Fuel Route API 🚛⛽

A high-performance Django REST API that computes optimal fuel stops for a road trip across the USA, minimizing total fuel cost while respecting a 500-mile vehicle range.

## Features

- 🗺️ **Routing**: Uses [OSRM](http://project-osrm.org/) (100% free, no API key needed) for accurate driving directions
- ⛽ **Fuel Optimization**: Greedy cheapest-in-range algorithm finds the lowest-cost fuel stops
- 📊 **8,000+ Stations**: Pre-loaded from OPIS truck stop database with real fuel prices
- 🔄 **Caching**: Route results cached for 1 hour; geocodes cached for 24 hours
- ⚡ **Fast**: ~0.25s for first request (uncached), ~0.07s for cached routes
- 🗺️ **Interactive Map**: Returns a [geojson.io](https://geojson.io) URL to visualize route + stops

## Architecture

```
┌─────────────────────────────────────────────┐
│                  Django API                   │
│  GET/POST /api/route/?start=...&end=...       │
└─────────────────────────────────────────────┘
         │                │
         ▼                ▼
   [Nominatim]       [File Cache]
   Geocode start/     (1h TTL)
   end locations
         │
         ▼
    [OSRM API]  ← 1 API call for full route geometry
         │
         ▼
   [SQLite DB]  ← Query 6,800+ geocoded fuel stations
         │
         ▼
  [NumPy Optimizer]  ← Vectorized spatial projection
         │           + greedy cheapest-in-range stops
         ▼
   JSON Response
```

### External API Calls Per Request
| Call | Purpose | Cached |
|------|---------|--------|
| Nominatim #1 | Geocode start location | ✅ 24h |
| Nominatim #2 | Geocode end location | ✅ 24h |
| OSRM | Full driving route | ✅ 1h |
| **Total** | **≤ 3 calls (1 if cached)** | |

## Quick Start

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Set up database
```bash
python manage.py migrate
python manage.py load_fuel_data          # Load 6,967 stations from CSV
python manage.py geocode_stations        # Geocode using US cities dataset (offline, ~2s)
```

### 3. Run the server
```bash
python manage.py runserver
```

### 4. Make a request
```bash
# GET request
curl "http://localhost:8000/api/route/?start=New+York,+NY&end=Los+Angeles,+CA"

# POST request
curl -X POST http://localhost:8000/api/route/ \
  -H "Content-Type: application/json" \
  -d '{"start": "Chicago, IL", "end": "Houston, TX"}'
```

## API Reference

### `GET /api/route/`
### `POST /api/route/`

**Request Parameters:**

| Field | Type | Description |
|-------|------|-------------|
| `start` | string | Starting US location (e.g., "New York, NY") |
| `end` | string | Destination US location (e.g., "Los Angeles, CA") |

**Response:**

```json
{
  "start_location": "New York, NY",
  "end_location": "Los Angeles, CA",
  "total_distance_miles": 2794.0,
  "estimated_duration_hours": 49.81,
  "total_gallons_needed": 279.4,
  "total_fuel_cost_usd": 748.61,
  "average_price_per_gallon": 2.6793,
  "fuel_stops_count": 12,
  "fuel_stops": [
    {
      "station_id": 6459,
      "opis_id": 72445,
      "name": "SHEETZ #639",
      "address": "I-80 Exit 223",
      "city": "Youngstown",
      "state": "OH",
      "latitude": 41.0986,
      "longitude": -80.6474,
      "retail_price_per_gallon": 3.059,
      "gallons_to_fill": 38.041,
      "cost_at_stop": 116.37,
      "route_distance_miles": 380.4,
      "miles_off_route": 3.8
    }
  ],
  "interactive_map_url": "https://geojson.io/#data=...",
  "static_map_url": "https://www.openstreetmap.org/?bbox=...",
  "route_geometry": {
    "type": "LineString",
    "coordinates": [...]
  },
  "_meta": {
    "processing_time_seconds": 0.25,
    "stations_considered": 1264,
    "vehicle_mpg": 10,
    "vehicle_max_range_miles": 500,
    "tank_size_gallons": 50
  }
}
```

## Fuel Optimization Algorithm

1. **Fetch candidates**: Query SQLite for all geocoded stations within the route's bounding box
2. **Simplify route**: Sample 1 in 30 waypoints (34K → ~1100 points) for fast spatial math
3. **Project stations**: Vectorized NumPy nearest-neighbor projection onto route polyline
4. **Filter corridor**: Keep only stations within ~75 miles of the route
5. **Greedy selection**:
   - Start at position 0, full tank (500 mi range)
   - At each step, find all reachable stations in the next 500 miles
   - Pick the **cheapest** station from which we can still continue
   - Fill up to full tank at each stop
   - Repeat until the destination is within range

## Vehicle Assumptions
- Max range: **500 miles** (full tank)
- Fuel economy: **10 MPG**
- Tank size: **50 gallons** (500 mi ÷ 10 mpg)

## Management Commands

```bash
# Load fuel station data from CSV
python manage.py load_fuel_data [--csv-path path/to/file.csv] [--clear]

# Geocode stations (offline, uses US cities database)
python manage.py geocode_stations [--no-resume] [--download]
```

## Data Sources
- **Fuel prices**: OPIS Truckstop dataset (provided in `fuel-prices-for-be-assessment.csv`)
- **Routing**: [OSRM](http://router.project-osrm.org) — free, open-source routing engine
- **Geocoding (locations)**: [Nominatim](https://nominatim.openstreetmap.org) — free OSM geocoder
- **Geocoding (stations)**: [US Cities Database](https://github.com/kelvins/US-Cities-Database) — static dataset (no API calls)

## Tech Stack
- Django 6.1 + Django REST Framework 3.18
- SQLite (easily swappable to PostgreSQL)
- NumPy for vectorized spatial math
- OSRM for routing (free, no API key)
- Nominatim for location geocoding (free, no API key)
- File-based cache (Django's built-in)
