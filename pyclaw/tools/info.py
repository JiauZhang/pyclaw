from datetime import datetime

import aiohttp
from chatchat.tool import tool


@tool(
    name='datetime',
    description='Get the current date and/or time in various formats.',
    parameters={
        'type': 'object',
        'properties': {
            'format': {
                'type': 'string',
                'description': 'Output format: iso, date, time, human, weekday',
                'default': 'human',
            },
        },
        'required': [],
    }
)
def datetime_tool(format: str = 'human'):
    now = datetime.now()
    if format == 'iso':
        return now.isoformat()
    elif format == 'date':
        return now.date().isoformat()
    elif format == 'time':
        return now.strftime('%H:%M:%S')
    elif format == 'human':
        return now.strftime('%Y-%m-%d %H:%M:%S')
    elif format == 'weekday':
        return now.strftime('%A')
    else:
        try:
            return now.strftime(format)
        except Exception as e:
            return f'Error: invalid format: {e}'


WEATHER_CODES = {
    0: 'Clear sky',
    1: 'Mainly clear',
    2: 'Partly cloudy',
    3: 'Overcast',
    45: 'Fog',
    48: 'Depositing rime fog',
    51: 'Light drizzle',
    53: 'Moderate drizzle',
    55: 'Dense drizzle',
    56: 'Light freezing drizzle',
    57: 'Dense freezing drizzle',
    61: 'Slight rain',
    63: 'Moderate rain',
    65: 'Heavy rain',
    66: 'Light freezing rain',
    67: 'Heavy freezing rain',
    71: 'Slight snow',
    73: 'Moderate snow',
    75: 'Heavy snow',
    77: 'Snow grains',
    80: 'Slight rain showers',
    81: 'Moderate rain showers',
    82: 'Violent rain showers',
    85: 'Slight snow showers',
    86: 'Heavy snow showers',
    95: 'Thunderstorm',
    96: 'Thunderstorm with slight hail',
    99: 'Thunderstorm with heavy hail',
}


async def _get_location_coords(location: str):
    url = 'https://geocoding-api.open-meteo.com/v1/search'
    params = {'name': location, 'count': 1, 'language': 'en', 'format': 'json'}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
        async with session.get(url, params=params) as resp:
            data = await resp.json()
    results = data.get('results', [])
    if not results:
        return None
    result = results[0]
    return {
        'lat': result.get('latitude'),
        'lon': result.get('longitude'),
        'name': result.get('name', location),
        'country': result.get('country', ''),
    }


@tool(
    name='geocode',
    description='Convert a city name to geographic coordinates (latitude, longitude) using geocoding.',
    parameters={
        'type': 'object',
        'properties': {
            'location': {
                'type': 'string',
                'description': 'City name in English (e.g., Beijing, Shanghai, New York).',
            },
        },
        'required': ['location'],
    }
)
async def geocode_tool(location: str):
    if not location:
        return 'Error: location is required.'
    coords = await _get_location_coords(location)
    if coords is None:
        return f'Error: could not find location: {location}'
    return f"Name: {coords['name']}\nCountry: {coords['country']}\nLatitude: {coords['lat']}\nLongitude: {coords['lon']}"


async def _get_weather_data(lat: float, lon: float):
    url = 'https://api.open-meteo.com/v1/forecast'
    params = {
        'latitude': lat,
        'longitude': lon,
        'current': 'temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m,wind_direction_10m',
        'timezone': 'auto',
    }
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        async with session.get(url, params=params) as resp:
            return await resp.json()


async def _get_ip_location():
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
        async with session.get('https://ipapi.co/json/') as resp:
            data = await resp.json()
    lat = data.get('latitude')
    lon = data.get('longitude')
    city = data.get('city', '')
    country = data.get('country_name', '')
    if lat is None or lon is None:
        raise RuntimeError('cannot detect location')
    return {
        'lat': lat,
        'lon': lon,
        'name': city,
        'country': country,
    }


@tool(
    name='location',
    description='Get the current geographic location (city, country, latitude, longitude) based on IP address.',
    parameters={
        'type': 'object',
        'properties': {},
        'required': [],
    }
)
async def location_tool():
    try:
        loc = await _get_ip_location()
        return f"City: {loc['name']}\nCountry: {loc['country']}\nLatitude: {loc['lat']}\nLongitude: {loc['lon']}"
    except Exception as e:
        return f'Error detecting location: {str(e)}'


@tool(
    name='weather',
    description='Query current weather information for a city or location. Use English city names (e.g., Shanghai, Beijing, Tokyo).',
    parameters={
        'type': 'object',
        'properties': {
            'location': {
                'type': 'string',
                'description': 'City name in English (e.g., Beijing, Shanghai, New York). Optional - if not provided, will auto-detect.',
            },
        },
        'required': [],
    }
)
async def weather_tool(location: str = ''):
    if not location:
        try:
            loc = await _get_ip_location()
            lat = loc['lat']
            lon = loc['lon']
            display_name = f"{loc['name']}, {loc['country']}" if loc['name'] and loc['country'] else (loc['name'] or loc['country'] or 'Unknown')
        except Exception:
            return 'Error: cannot auto-detect location. Please provide a city name.'
    else:
        coords = await _get_location_coords(location)
        if coords is None:
            return f'Error: could not find location: {location}'
        lat = coords['lat']
        lon = coords['lon']
        name = coords['name']
        country = coords['country']
        display_name = f'{name}, {country}' if country else name

    try:
        data = await _get_weather_data(lat, lon)
        current = data.get('current', {})
        temp = current.get('temperature_2m', 'N/A')
        feels_like = current.get('apparent_temperature', 'N/A')
        humidity = current.get('relative_humidity_2m', 'N/A')
        weather_code = current.get('weather_code', -1)
        wind_speed = current.get('wind_speed_10m', 'N/A')
        wind_dir = current.get('wind_direction_10m', 'N/A')
        weather_desc = WEATHER_CODES.get(weather_code, f'Unknown (code: {weather_code})')
        return f'''Location: {display_name}
Temperature: {temp}C (feels like {feels_like}C)
Weather: {weather_desc}
Humidity: {humidity}%
Wind: {wind_speed} km/h, direction: {wind_dir}'''
    except Exception as e:
        return f'Error fetching weather: {str(e)}'
