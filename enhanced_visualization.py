"""
Enhanced 3D Visualization Module
Provides realistic rendering effects for heat flow visualization
"""

import numpy as np
from typing import Dict, List, Optional, Tuple
import json
import logging

class EnhancedVisualization:
    """
    Enhanced visualization utilities for realistic 3D heat flow rendering
    """
    
    def __init__(self):
        """Initialize enhanced visualization"""
        self.temperature_colormap = self._create_advanced_colormap()
        self.material_shaders = self._define_material_shaders()
        self.heat_effects = self._setup_heat_effects()
    
    def _create_advanced_colormap(self) -> List[Dict]:
        """
        Create advanced temperature colormap with realistic heat colors
        
        Returns:
            List of color stops with temperature ranges
        """
        return [
            # Cold temperatures (blue spectrum)
            {'temp': 25, 'color': [0.0, 0.2, 0.8], 'emission': 0.0, 'metallic': 0.8, 'roughness': 0.3},
            {'temp': 100, 'color': [0.0, 0.4, 1.0], 'emission': 0.0, 'metallic': 0.7, 'roughness': 0.3},
            
            # Warm temperatures (green to yellow)
            {'temp': 200, 'color': [0.0, 0.8, 0.4], 'emission': 0.1, 'metallic': 0.6, 'roughness': 0.4},
            {'temp': 300, 'color': [0.4, 1.0, 0.0], 'emission': 0.2, 'metallic': 0.5, 'roughness': 0.5},
            {'temp': 400, 'color': [1.0, 1.0, 0.0], 'emission': 0.3, 'metallic': 0.4, 'roughness': 0.6},
            
            # Hot temperatures (orange to red)
            {'temp': 500, 'color': [1.0, 0.6, 0.0], 'emission': 0.5, 'metallic': 0.3, 'roughness': 0.7},
            {'temp': 600, 'color': [1.0, 0.2, 0.0], 'emission': 0.7, 'metallic': 0.2, 'roughness': 0.8},
            
            # Very hot (molten state)
            {'temp': 700, 'color': [1.0, 0.4, 0.4], 'emission': 0.9, 'metallic': 0.1, 'roughness': 0.9},
            {'temp': 800, 'color': [1.0, 0.8, 0.8], 'emission': 1.0, 'metallic': 0.0, 'roughness': 1.0},
            {'temp': 1000, 'color': [1.0, 1.0, 1.0], 'emission': 1.0, 'metallic': 0.0, 'roughness': 1.0}
        ]
    
    def _define_material_shaders(self) -> Dict:
        """
        Define realistic material shaders for different alloys
        
        Returns:
            Dictionary of material shader properties
        """
        return {
            '6061-T6': {
                'base_color': [0.7, 0.7, 0.8],
                'metallic': 0.9,
                'roughness': 0.1,
                'specular': 0.9,
                'clearcoat': 0.1,
                'anisotropy': 0.2,
                'normal_intensity': 0.5
            },
            '3003-O': {
                'base_color': [0.8, 0.8, 0.7],
                'metallic': 0.85,
                'roughness': 0.15,
                'specular': 0.85,
                'clearcoat': 0.05,
                'anisotropy': 0.1,
                'normal_intensity': 0.3
            },
            'AL718': {
                'base_color': [0.9, 0.8, 0.6],
                'metallic': 0.8,
                'roughness': 0.2,
                'specular': 0.8,
                'clearcoat': 0.0,
                'anisotropy': 0.0,
                'normal_intensity': 0.2
            }
        }
    
    def _setup_heat_effects(self) -> Dict:
        """
        Setup heat visualization effects
        
        Returns:
            Dictionary of heat effect parameters
        """
        return {
            'heat_distortion': {
                'enabled': True,
                'intensity': 0.02,
                'frequency': 2.0,
                'speed': 1.0
            },
            'thermal_glow': {
                'enabled': True,
                'threshold_temp': 400.0,
                'glow_intensity': 2.0,
                'glow_radius': 0.05
            },
            'particle_effects': {
                'enabled': True,
                'emission_temp': 600.0,
                'particle_count': 100,
                'particle_life': 2.0
            },
            'heat_waves': {
                'enabled': True,
                'wave_height': 0.001,
                'wave_frequency': 5.0,
                'wave_speed': 2.0
            }
        }
    
    def interpolate_temperature_color(self, temperature: float) -> Dict:
        """
        Interpolate color and material properties based on temperature
        
        Args:
            temperature: Temperature in Celsius
            
        Returns:
            Dictionary with color and material properties
        """
        colormap = self.temperature_colormap
        
        # Clamp temperature to colormap range
        min_temp = colormap[0]['temp']
        max_temp = colormap[-1]['temp']
        temp = max(min_temp, min(max_temp, temperature))
        
        # Find surrounding color stops
        for i in range(len(colormap) - 1):
            if colormap[i]['temp'] <= temp <= colormap[i + 1]['temp']:
                # Interpolate between these two stops
                t1, t2 = colormap[i]['temp'], colormap[i + 1]['temp']
                factor = (temp - t1) / (t2 - t1) if t2 != t1 else 0
                
                # Interpolate all properties
                result = {}
                for key in ['color', 'emission', 'metallic', 'roughness']:
                    if key == 'color':
                        # Interpolate RGB values
                        c1, c2 = colormap[i][key], colormap[i + 1][key]
                        result[key] = [
                            c1[0] + factor * (c2[0] - c1[0]),
                            c1[1] + factor * (c2[1] - c1[1]),
                            c1[2] + factor * (c2[2] - c1[2])
                        ]
                    else:
                        # Interpolate scalar values
                        v1, v2 = colormap[i][key], colormap[i + 1][key]
                        result[key] = v1 + factor * (v2 - v1)
                
                return result
        
        # Fallback to last color stop
        return {
            'color': colormap[-1]['color'],
            'emission': colormap[-1]['emission'],
            'metallic': colormap[-1]['metallic'],
            'roughness': colormap[-1]['roughness']
        }
    
    def generate_vertex_colors(self, temperature_field: List[float]) -> List[List[float]]:
        """
        Generate vertex colors based on temperature field
        
        Args:
            temperature_field: List of temperatures for each vertex
            
        Returns:
            List of RGB colors for each vertex
        """
        colors = []
        for temp in temperature_field:
            color_data = self.interpolate_temperature_color(temp)
            colors.append(color_data['color'])
        
        return colors
    
    def generate_material_properties(self, temperature_field: List[float]) -> Dict:
        """
        Generate material properties for realistic rendering
        
        Args:
            temperature_field: List of temperatures for each vertex
            
        Returns:
            Dictionary with material property arrays
        """
        properties = {
            'colors': [],
            'emissions': [],
            'metallics': [],
            'roughness': []
        }
        
        for temp in temperature_field:
            color_data = self.interpolate_temperature_color(temp)
            properties['colors'].append(color_data['color'])
            properties['emissions'].append(color_data['emission'])
            properties['metallics'].append(color_data['metallic'])
            properties['roughness'].append(color_data['roughness'])
        
        return properties
    
    def create_heat_flow_vectors(self, vertices: List[List[float]], 
                               temperature_field: List[float]) -> List[Dict]:
        """
        Create heat flow vectors for visualization
        
        Args:
            vertices: Mesh vertices
            temperature_field: Temperature at each vertex
            
        Returns:
            List of heat flow vectors
        """
        if len(vertices) < 2 or len(temperature_field) < 2:
            return []
        
        vertices_np = np.array(vertices)
        temps_np = np.array(temperature_field)
        
        heat_vectors = []
        
        # Calculate temperature gradients
        for i, vertex in enumerate(vertices_np):
            # Find nearby vertices
            distances = np.linalg.norm(vertices_np - vertex, axis=1)
            nearby_indices = np.where((distances > 0) & (distances < 0.02))[0]  # 2cm radius
            
            if len(nearby_indices) > 0:
                # Calculate gradient
                gradient = np.zeros(3)
                for j in nearby_indices:
                    direction = vertices_np[j] - vertex
                    distance = np.linalg.norm(direction)
                    if distance > 0:
                        direction_normalized = direction / distance
                        temp_diff = temps_np[j] - temps_np[i]
                        gradient += direction_normalized * temp_diff / distance
                
                # Heat flows from hot to cold (opposite of temperature gradient)
                heat_flow = -gradient
                magnitude = np.linalg.norm(heat_flow)
                
                if magnitude > 0.1:  # Only show significant heat flow
                    heat_vectors.append({
                        'position': vertex.tolist(),
                        'direction': (heat_flow / magnitude).tolist(),
                        'magnitude': float(magnitude),
                        'temperature': float(temps_np[i])
                    })
        
        return heat_vectors
    
    def generate_shader_code(self, material_type: str = '6061-T6') -> Dict:
        """
        Generate shader code for realistic material rendering
        
        Args:
            material_type: Type of material
            
        Returns:
            Dictionary with vertex and fragment shader code
        """
        material_props = self.material_shaders.get(material_type, self.material_shaders['6061-T6'])
        
        vertex_shader = """
        attribute vec3 position;
        attribute vec3 normal;
        attribute vec2 uv;
        attribute float temperature;
        
        uniform mat4 modelViewMatrix;
        uniform mat4 projectionMatrix;
        uniform mat3 normalMatrix;
        uniform float time;
        
        varying vec3 vPosition;
        varying vec3 vNormal;
        varying vec2 vUv;
        varying float vTemperature;
        varying vec3 vWorldPosition;
        
        void main() {
            vUv = uv;
            vTemperature = temperature;
            vNormal = normalize(normalMatrix * normal);
            
            // Heat distortion effect
            vec3 distortedPosition = position;
            if (temperature > 400.0) {
                float distortionAmount = (temperature - 400.0) / 400.0 * 0.002;
                distortedPosition += normal * sin(time * 3.0 + position.x * 10.0) * distortionAmount;
            }
            
            vec4 worldPosition = modelViewMatrix * vec4(distortedPosition, 1.0);
            vWorldPosition = worldPosition.xyz;
            vPosition = worldPosition.xyz;
            
            gl_Position = projectionMatrix * worldPosition;
        }
        """
        
        fragment_shader = f"""
        precision highp float;
        
        uniform float time;
        uniform vec3 cameraPosition;
        
        varying vec3 vPosition;
        varying vec3 vNormal;
        varying vec2 vUv;
        varying float vTemperature;
        varying vec3 vWorldPosition;
        
        // Material properties
        const vec3 baseColor = vec3({material_props['base_color'][0]}, {material_props['base_color'][1]}, {material_props['base_color'][2]});
        const float basemetallic = {material_props['metallic']};
        const float baseRoughness = {material_props['roughness']};
        
        // Temperature color mapping
        vec3 temperatureToColor(float temp) {{
            if (temp < 100.0) return mix(vec3(0.0, 0.2, 0.8), vec3(0.0, 0.4, 1.0), (temp - 25.0) / 75.0);
            else if (temp < 300.0) return mix(vec3(0.0, 0.4, 1.0), vec3(0.4, 1.0, 0.0), (temp - 100.0) / 200.0);
            else if (temp < 500.0) return mix(vec3(0.4, 1.0, 0.0), vec3(1.0, 0.6, 0.0), (temp - 300.0) / 200.0);
            else if (temp < 700.0) return mix(vec3(1.0, 0.6, 0.0), vec3(1.0, 0.2, 0.0), (temp - 500.0) / 200.0);
            else return mix(vec3(1.0, 0.2, 0.0), vec3(1.0, 1.0, 1.0), min((temp - 700.0) / 300.0, 1.0));
        }}
        
        float temperatureToEmission(float temp) {{
            return smoothstep(400.0, 800.0, temp);
        }}
        
        void main() {{
            vec3 normal = normalize(vNormal);
            vec3 viewDirection = normalize(cameraPosition - vWorldPosition);
            
            // Base material color modified by temperature
            vec3 tempColor = temperatureToColor(vTemperature);
            vec3 finalColor = mix(baseColor, tempColor, 0.7);
            
            // Emission based on temperature
            float emission = temperatureToEmission(vTemperature);
            vec3 emissiveColor = tempColor * emission;
            
            // Simple Fresnel effect
            float fresnel = pow(1.0 - max(dot(normal, viewDirection), 0.0), 2.0);
            
            // Heat glow effect for high temperatures
            float glowIntensity = smoothstep(500.0, 800.0, vTemperature);
            vec3 glow = tempColor * glowIntensity * 0.5;
            
            // Combine all effects
            vec3 result = finalColor + emissiveColor + glow;
            result += fresnel * 0.1;
            
            gl_FragColor = vec4(result, 1.0);
        }}
        """
        
        return {
            'vertex': vertex_shader,
            'fragment': fragment_shader,
            'uniforms': {
                'time': 0.0,
                'cameraPosition': [0, 0, 5]
            }
        }
    
    def create_visualization_config(self, mesh_data: Dict, 
                                  temperature_field: List[float]) -> Dict:
        """
        Create complete visualization configuration
        
        Args:
            mesh_data: Mesh geometry data
            temperature_field: Temperature values
            
        Returns:
            Complete visualization configuration
        """
        # Generate colors and material properties
        material_props = self.generate_material_properties(temperature_field)
        heat_vectors = self.create_heat_flow_vectors(
            mesh_data.get('vertices', []), 
            temperature_field
        )
        
        # Calculate statistics
        if temperature_field:
            max_temp = max(temperature_field)
            min_temp = min(temperature_field)
            avg_temp = sum(temperature_field) / len(temperature_field)
        else:
            max_temp = min_temp = avg_temp = 25.0
        
        return {
            'mesh': {
                'vertices': mesh_data.get('vertices', []),
                'faces': mesh_data.get('faces', []),
                'normals': self._calculate_normals(mesh_data),
                'uvs': self._generate_uvs(mesh_data)
            },
            'materials': {
                'colors': material_props['colors'],
                'emissions': material_props['emissions'],
                'metallics': material_props['metallics'],
                'roughness': material_props['roughness']
            },
            'effects': {
                'heat_vectors': heat_vectors,
                'heat_distortion': self.heat_effects['heat_distortion'],
                'thermal_glow': self.heat_effects['thermal_glow'],
                'particle_effects': self.heat_effects['particle_effects']
            },
            'statistics': {
                'max_temp': max_temp,
                'min_temp': min_temp,
                'avg_temp': avg_temp,
                'vertex_count': len(mesh_data.get('vertices', [])),
                'face_count': len(mesh_data.get('faces', []))
            },
            'shaders': self.generate_shader_code(),
            'colormap': self.temperature_colormap
        }
    
    def _calculate_normals(self, mesh_data: Dict) -> List[List[float]]:
        """Calculate vertex normals for lighting"""
        vertices = np.array(mesh_data.get('vertices', []))
        faces = np.array(mesh_data.get('faces', []))
        
        if len(vertices) == 0 or len(faces) == 0:
            return [[0, 0, 1]] * len(vertices)
        
        # Initialize normals
        normals = np.zeros_like(vertices)
        
        # Calculate face normals and accumulate at vertices
        for face in faces:
            if len(face) >= 3:
                v0, v1, v2 = vertices[face[0]], vertices[face[1]], vertices[face[2]]
                face_normal = np.cross(v1 - v0, v2 - v0)
                face_normal = face_normal / (np.linalg.norm(face_normal) + 1e-8)
                
                for vertex_idx in face:
                    normals[vertex_idx] += face_normal
        
        # Normalize vertex normals
        for i in range(len(normals)):
            norm = np.linalg.norm(normals[i])
            if norm > 0:
                normals[i] /= norm
            else:
                normals[i] = [0, 0, 1]
        
        return normals.tolist()
    
    def _generate_uvs(self, mesh_data: Dict) -> List[List[float]]:
        """Generate UV coordinates for texturing"""
        vertices = mesh_data.get('vertices', [])
        if not vertices:
            return []
        
        # Simple planar projection
        vertices_np = np.array(vertices)
        
        # Project onto XY plane
        min_x, max_x = vertices_np[:, 0].min(), vertices_np[:, 0].max()
        min_y, max_y = vertices_np[:, 1].min(), vertices_np[:, 1].max()
        
        uvs = []
        for vertex in vertices_np:
            u = (vertex[0] - min_x) / (max_x - min_x + 1e-8)
            v = (vertex[1] - min_y) / (max_y - min_y + 1e-8)
            uvs.append([u, v])
        
        return uvs
