"""Weather tool."""

import asyncio
import ssl
from typing import Any, Dict

import httpx

from ..base import Tool, ToolResult


# 天气代码映射 (WMO Weather interpretation codes)
WEATHER_CODES = {
    0: "晴朗",
    1: "大部晴朗",
    2: "局部多云",
    3: "多云",
    45: "雾",
    48: "雾凇",
    51: "毛毛雨 (轻)",
    53: "毛毛雨 (中)",
    55: "毛毛雨 (大)",
    56: "冻雨 (轻)",
    57: "冻雨 (大)",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "冻雨 (轻)",
    67: "冻雨 (大)",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "雪粒",
    80: "阵雨 (轻)",
    81: "阵雨 (中)",
    82: "阵雨 (大)",
    85: "阵雪 (轻)",
    86: "阵雪 (大)",
    95: "雷雨",
    96: "雷雨伴冰雹 (轻)",
    99: "雷雨伴冰雹 (大)",
}


def get_weather_desc(code: int) -> str:
    """Get weather description from code."""
    return WEATHER_CODES.get(code, f"未知天气 (代码: {code})")


class WeatherTool(Tool):
    """Query weather information for a location using Open-Meteo API."""

    def __init__(self):
        super().__init__(
            name="weather",
            description="Query current weather information for a city or location. Returns temperature, weather conditions, and other details. If no location is provided, will automatically detect your location.",
            parameters={
                "location": {
                    "type": "string",
                    "description": "City name or location (e.g., 'Beijing', 'New York', 'London'). Optional - if not provided, will auto-detect your location."
                }
            },
            required_params=[]
        )

    async def _get_coordinates(self, location: str) -> tuple[float, float, str] | None:
        """Get coordinates from location name using geocoding API."""
        # 常见城市名映射（解决繁体中文或别名问题）
        city_mapping = {
            "上海": "Shanghai",
            "北京": "Beijing",
            "广州": "Guangzhou",
            "深圳": "Shenzhen",
            "杭州": "Hangzhou",
            "南京": "Nanjing",
            "成都": "Chengdu",
            "武汉": "Wuhan",
            "西安": "Xi'an",
            "重庆": "Chongqing",
            "天津": "Tianjin",
            "苏州": "Suzhou",
            "香港": "Hong Kong",
            "台北": "Taipei",
        }
        
        # 尝试使用映射后的英文名
        search_names = [location]
        if location in city_mapping:
            search_names.append(city_mapping[location])
        
        for search_name in search_names:
            result = await self._search_location(search_name)
            if result:
                return result
        
        return None

    async def _get_public_ip_and_location(self) -> tuple[float, float, str] | None:
        """Get public IP address and location in one call using ipapi.co."""
        try:
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(10.0, connect=5.0),
                verify=ssl_context
            ) as client:
                response = await client.get("https://ipapi.co/json/")
                if response.status_code != 200:
                    return None
                
                data = response.json()
                
                lat = data.get("latitude")
                lon = data.get("longitude")
                city = data.get("city", "")
                country = data.get("country_name", "")
                
                if lat is None or lon is None:
                    return None
                
                display_name = f"{city}, {country}" if city and country else (city or country or "未知位置")
                return (lat, lon, display_name)
                
        except Exception:
            pass
        return None
    
    async def _search_location(self, location: str) -> tuple[float, float, str] | None:
        """Search for a single location."""
        try:
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(10.0, connect=5.0),
                verify=ssl_context
            ) as client:
                url = "https://geocoding-api.open-meteo.com/v1/search"
                params = {
                    "name": location,
                    "count": 1,
                    "language": "zh",
                    "format": "json"
                }
                response = await client.get(url, params=params)
                
                if response.status_code != 200:
                    return None
                
                data = response.json()
                results = data.get("results", [])
                
                if not results:
                    return None
                
                result = results[0]
                lat = result.get("latitude")
                lon = result.get("longitude")
                name = result.get("name", location)
                country = result.get("country", "")
                
                display_name = f"{name}, {country}" if country else name
                return (lat, lon, display_name)
                
        except Exception:
            return None

    async def _get_weather(self, lat: float, lon: float) -> dict | None:
        """Get weather data from coordinates."""
        try:
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(15.0, connect=5.0, read=10.0),
                verify=ssl_context
            ) as client:
                url = "https://api.open-meteo.com/v1/forecast"
                params = {
                    "latitude": lat,
                    "longitude": lon,
                    "current": ["temperature_2m", "relative_humidity_2m", "apparent_temperature", 
                               "weather_code", "wind_speed_10m", "wind_direction_10m"],
                    "timezone": "auto"
                }
                response = await client.get(url, params=params)
                
                if response.status_code != 200:
                    return None
                
                return response.json()
                
        except Exception:
            return None

    async def execute(self, arguments: Dict[str, Any], context: Any) -> ToolResult:
        location = arguments.get("location", "")
        
        # If no location provided, try to auto-detect
        if not location:
            coords = await self._get_public_ip_and_location()
            if coords:
                lat, lon, display_name = coords
                location = display_name
            else:
                return ToolResult(
                    output="",
                    error="无法自动获取您的位置。请提供城市名称。"
                )
        else:
            # Step 1: Get coordinates from provided location
            coords = await self._get_coordinates(location)
            if not coords:
                return ToolResult(
                    output="",
                    error=f"Could not find location: {location}. Please try a different city name."
                )
            lat, lon, display_name = coords
        
        # Step 2: Get weather data
        weather_data = await self._get_weather(lat, lon)
        if not weather_data:
            return ToolResult(
                output="",
                error="Failed to fetch weather data. Please try again later."
            )
        
        # Parse weather data
        current = weather_data.get("current", {})
        
        temp = current.get("temperature_2m", "N/A")
        feels_like = current.get("apparent_temperature", "N/A")
        humidity = current.get("relative_humidity_2m", "N/A")
        weather_code = current.get("weather_code", -1)
        wind_speed = current.get("wind_speed_10m", "N/A")
        wind_dir = current.get("wind_direction_10m", "N/A")
        
        weather_desc = get_weather_desc(weather_code)
        
        # Format wind direction
        wind_direction_str = self._format_wind_direction(wind_dir)
        
        result = f"""📍 位置: {display_name}
🌡️ 温度: {temp}°C (体感温度: {feels_like}°C)
🌤️ 天气: {weather_desc}
💧 湿度: {humidity}%
💨 风速: {wind_speed} km/h, 风向: {wind_direction_str}"""
        
        return ToolResult(output=result)
    
    def _format_wind_direction(self, degrees: Any) -> str:
        """Convert wind direction degrees to cardinal direction."""
        if degrees == "N/A" or degrees is None:
            return "未知"
        
        try:
            deg = float(degrees)
            directions = ["北", "东北", "东", "东南", "南", "西南", "西", "西北"]
            index = round(deg / 45) % 8
            return directions[index]
        except (ValueError, TypeError):
            return str(degrees)
