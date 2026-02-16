/**
 * Enhanced 3D Renderer for Realistic Heat Flow Visualization
 * Supports FEM-based simulation data with advanced material rendering
 */

class EnhancedRenderer {
    constructor(container) {
        this.container = container;
        this.scene = null;
        this.camera = null;
        this.renderer = null;
        this.controls = null;

        // Mesh and materials
        this.meshObject = null;
        this.heatVectorObjects = [];
        this.particleSystem = null;

        // Shaders and materials
        this.heatMaterial = null;
        this.customShaders = {};

        // Animation
        this.animationId = null;
        this.time = 0;

        // Heat visualization
        this.temperatureField = [];
        this.heatVectors = [];
        this.materialProperties = {};

        this.init();
    }

    init() {
        this.setupScene();
        this.setupLighting();
        this.setupPostProcessing();
        this.startRenderLoop();
    }

    setupScene() {
        // Create scene with enhanced settings and dark blue-gray background
        this.scene = new THREE.Scene();
        this.scene.background = new THREE.Color(0x1a202c);
        this.scene.fog = new THREE.Fog(0x1a202c, 1, 100);

        // Setup camera with better positioning
        const aspect = this.container.clientWidth / this.container.clientHeight;
        this.camera = new THREE.PerspectiveCamera(45, aspect, 0.1, 5000);
        this.camera.position.set(5, 5, 5);
        this.camera.lookAt(0, 0, 0);

        // Create renderer with advanced settings
        this.renderer = new THREE.WebGLRenderer({
            antialias: true,
            powerPreference: 'high-performance',
            alpha: false,
            stencil: false,
            depth: true,
            logarithmicDepthBuffer: true
        });

        this.renderer.setSize(this.container.clientWidth, this.container.clientHeight);
        this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

        // Enable advanced rendering features
        this.renderer.shadowMap.enabled = true;
        this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
        this.renderer.outputEncoding = THREE.sRGBEncoding;
        this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
        this.renderer.toneMappingExposure = 1.2;

        // Add to container
        this.container.appendChild(this.renderer.domElement);

        // Setup controls
        this.controls = new THREE.OrbitControls(this.camera, this.renderer.domElement);
        this.controls.enableDamping = true;
        this.controls.dampingFactor = 0.05;
        this.controls.maxDistance = 5000;
        this.controls.minDistance = 1;
        this.controls.autoRotate = false;  // Disabled to allow manual control
        this.controls.autoRotateSpeed = 1.0;

        // Enable all mouse interactions
        this.controls.enableZoom = true;      // Mouse wheel zoom
        this.controls.enableRotate = true;    // Left-click drag to rotate
        this.controls.enablePan = true;       // Right-click drag to pan
        this.controls.mouseButtons = {
            LEFT: THREE.MOUSE.ROTATE,
            MIDDLE: THREE.MOUSE.DOLLY,
            RIGHT: THREE.MOUSE.PAN
        };
        this.controls.touches = {
            ONE: THREE.TOUCH.ROTATE,
            TWO: THREE.TOUCH.DOLLY_PAN
        };

        // Handle resize
        window.addEventListener('resize', () => this.onWindowResize());
    }

    setupLighting() {
        // Ambient light for base illumination
        const ambientLight = new THREE.AmbientLight(0x404040, 0.3);
        this.scene.add(ambientLight);

        // Main directional light
        const mainLight = new THREE.DirectionalLight(0xffffff, 1.0);
        mainLight.position.set(10, 10, 5);
        mainLight.castShadow = true;
        mainLight.shadow.mapSize.width = 2048;
        mainLight.shadow.mapSize.height = 2048;
        mainLight.shadow.camera.near = 0.5;
        mainLight.shadow.camera.far = 50;
        this.scene.add(mainLight);

        // Fill light
        const fillLight = new THREE.DirectionalLight(0x4080ff, 0.3);
        fillLight.position.set(-5, 5, -5);
        this.scene.add(fillLight);

        // Point lights for heat sources
        this.heatSourceLight = new THREE.PointLight(0xff4000, 0, 10);
        this.heatSourceLight.position.set(0, 0, 0);
        this.scene.add(this.heatSourceLight);

        // Environment map for reflections
        const pmremGenerator = new THREE.PMREMGenerator(this.renderer);
        const envTexture = pmremGenerator.fromScene(new THREE.Scene()).texture;
        this.scene.environment = envTexture;
    }

    setupPostProcessing() {
        // Setup post-processing for heat effects
        // This would typically use THREE.EffectComposer
        // For now, we'll handle effects in the main shader
    }

    createHeatMaterial() {
        // Create custom shader material for heat visualization
        const vertexShader = `
            attribute float temperature;
            attribute vec3 heatFlow;
            
            uniform float time;
            uniform float maxTemp;
            uniform float minTemp;
            
            varying vec3 vPosition;
            varying vec3 vNormal;
            varying vec2 vUv;
            varying float vTemperature;
            varying vec3 vHeatFlow;
            varying vec3 vWorldPosition;
            
            // Heat distortion function
            vec3 heatDistortion(vec3 pos, float temp) {
                float distortionAmount = smoothstep(400.0, 800.0, temp) * 0.002;
                float noise = sin(time * 3.0 + pos.x * 10.0 + pos.y * 8.0) * 
                             cos(time * 2.0 + pos.z * 12.0);
                return pos + normal * noise * distortionAmount;
            }
            
            void main() {
                vUv = uv;
                vTemperature = temperature;
                vHeatFlow = heatFlow;
                vNormal = normalize(normalMatrix * normal);
                
                // Apply heat distortion
                vec3 distortedPosition = heatDistortion(position, temperature);
                
                // Calculate world position for lighting
                vec4 worldPosition = modelMatrix * vec4(distortedPosition, 1.0);
                vWorldPosition = worldPosition.xyz;
                vPosition = worldPosition.xyz;
                
                // Calculate final position
                gl_Position = projectionMatrix * viewMatrix * worldPosition;
            }
        `;

        const fragmentShader = `
            uniform float time;
            // cameraPosition is built-in
            uniform float maxTemp;
            uniform float minTemp;
            
            varying vec3 vPosition;
            varying vec3 vNormal;
            varying vec2 vUv;
            varying float vTemperature;
            varying vec3 vHeatFlow;
            varying vec3 vWorldPosition;
            
            // Temperature to color mapping
            vec3 temperatureToColor(float temp) {
                float normalizedTemp = (temp - 25.0) / (800.0 - 25.0);
                normalizedTemp = clamp(normalizedTemp, 0.0, 1.0);
                
                vec3 cold = vec3(0.95, 0.95, 0.95);   // Off-white (room temp)
                vec3 warm = vec3(0.8, 0.95, 0.9);     // Light cyan-white
                vec3 hot = vec3(1.0, 0.7, 0.2);       // Orange
                vec3 veryHot = vec3(1.0, 0.3, 0.0);   // Red
                vec3 molten = vec3(1.0, 1.0, 1.0);    // White
                
                if (normalizedTemp < 0.25) {
                    return mix(cold, warm, normalizedTemp * 4.0);
                } else if (normalizedTemp < 0.5) {
                    return mix(warm, hot, (normalizedTemp - 0.25) * 4.0);
                } else if (normalizedTemp < 0.75) {
                    return mix(hot, veryHot, (normalizedTemp - 0.5) * 4.0);
                } else {
                    return mix(veryHot, molten, (normalizedTemp - 0.75) * 4.0);
                }
            }
            
            float temperatureToEmission(float temp) {
                return smoothstep(400.0, 800.0, temp);
            }
            
            float temperatureToMetallic(float temp) {
                return mix(0.9, 0.1, smoothstep(300.0, 700.0, temp));
            }
            
            float temperatureToRoughness(float temp) {
                return mix(0.1, 0.9, smoothstep(200.0, 600.0, temp));
            }
            
            uniform vec3 lightDirection;
            
            void main() {
                vec3 normal = normalize(vNormal);
                vec3 viewDirection = normalize(cameraPosition - vWorldPosition);
                vec3 lightDir = normalize(lightDirection);
                
                // Base color from temperature
                vec3 baseColor = temperatureToColor(vTemperature);
                
                // Material properties based on temperature
                // Reduce metallic at low temps to ensure visibility
                float metallic = mix(0.3, 0.1, smoothstep(300.0, 700.0, vTemperature));
                float roughness = temperatureToRoughness(vTemperature);
                float emission = temperatureToEmission(vTemperature);
                
                // Lighting calculations
                float NdotL = max(dot(normal, lightDir), 0.2); // Add ambient term (0.2)
                float NdotV = max(dot(normal, viewDirection), 0.0);
                float fresnel = pow(1.0 - NdotV, 2.0);
                
                // Diffuse component (Lambertian)
                vec3 diffuse = baseColor * (1.0 - metallic) * NdotL;
                
                // Specular component (Blinn-Phong approximation)
                vec3 halfVector = normalize(lightDir + viewDirection);
                float NdotH = max(dot(normal, halfVector), 0.0);
                float specularIntensity = pow(NdotH, 32.0 * (1.0 - roughness));
                vec3 specular = mix(vec3(0.04), baseColor, metallic) * specularIntensity;
                specular += mix(vec3(0.04), baseColor, metallic) * fresnel * 0.5; // Add fresnel glow
                
                // Emission component
                vec3 emissive = baseColor * emission * 2.0;
                
                // Heat glow effect
                float glowIntensity = smoothstep(500.0, 800.0, vTemperature);
                vec3 glow = baseColor * glowIntensity * 0.5;
                
                // Heat flow visualization
                float flowMagnitude = length(vHeatFlow);
                vec3 flowColor = vec3(1.0, 0.5, 0.0) * flowMagnitude * 0.1;
                
                // Combine all components
                vec3 finalColor = diffuse + specular + emissive + glow + flowColor;
                
                // Add some atmospheric scattering for realism
                float viewDistance = length(vWorldPosition - cameraPosition);
                float scattering = exp(-viewDistance * 0.01);
                finalColor = mix(vec3(0.1, 0.1, 0.15), finalColor, scattering);
                
                gl_FragColor = vec4(finalColor, 1.0);
            }
        `;

        this.heatMaterial = new THREE.ShaderMaterial({
            vertexShader: vertexShader,
            fragmentShader: fragmentShader,
            uniforms: {
                time: { value: 0.0 },
                cameraPosition: { value: this.camera.position },
                lightDirection: { value: new THREE.Vector3(10, 10, 5).normalize() },
                maxTemp: { value: 800.0 },
                minTemp: { value: 25.0 }
            },
            transparent: false,
            side: THREE.DoubleSide
        });

        return this.heatMaterial;
    }

    updateMesh(meshData, temperatureField) {
        // Remove existing mesh
        if (this.meshObject) {
            this.scene.remove(this.meshObject);
            this.meshObject.geometry.dispose();
            this.meshObject = null;
        }

        if (!meshData.vertices || meshData.vertices.length === 0) {
            console.warn('No mesh data available');
            return;
        }

        // Create geometry
        const geometry = new THREE.BufferGeometry();

        // Set vertices
        const vertices = new Float32Array(meshData.vertices.flat());
        geometry.setAttribute('position', new THREE.BufferAttribute(vertices, 3));

        // Set faces if available
        if (meshData.faces && meshData.faces.length > 0) {
            const indices = new Uint32Array(meshData.faces.flat());
            geometry.setIndex(new THREE.BufferAttribute(indices, 1));
        }

        // Set temperature attribute
        if (temperatureField && temperatureField.length > 0) {
            const temps = new Float32Array(temperatureField);
            geometry.setAttribute('temperature', new THREE.BufferAttribute(temps, 1));
            this.temperatureField = temperatureField;
        }

        // Calculate normals
        geometry.computeVertexNormals();

        // Set UV coordinates
        if (meshData.uvs) {
            const uvs = new Float32Array(meshData.uvs.flat());
            geometry.setAttribute('uv', new THREE.BufferAttribute(uvs, 2));
        } else {
            // Generate simple UV coordinates
            const uvArray = [];
            for (let i = 0; i < vertices.length / 3; i++) {
                uvArray.push(0, 0); // Simple UV mapping
            }
            geometry.setAttribute('uv', new THREE.BufferAttribute(new Float32Array(uvArray), 2));
        }

        // Create material
        const material = this.createHeatMaterial();

        // Create mesh
        this.meshObject = new THREE.Mesh(geometry, material);
        this.meshObject.castShadow = true;
        this.meshObject.receiveShadow = true;

        this.scene.add(this.meshObject);

        // Update camera to frame the object
        this.frameObject();
    }

    updateHeatVectors(heatVectors) {
        // Remove existing heat vectors
        this.heatVectorObjects.forEach(obj => {
            this.scene.remove(obj);
            obj.geometry.dispose();
            obj.material.dispose();
        });
        this.heatVectorObjects = [];

        if (!heatVectors || heatVectors.length === 0) return;

        // Create heat flow arrows
        const arrowGeometry = new THREE.ConeGeometry(0.002, 0.01, 8);
        const arrowMaterial = new THREE.MeshBasicMaterial({
            color: 0xff4000,
            transparent: true,
            opacity: 0.7
        });

        heatVectors.forEach(vector => {
            if (vector.magnitude > 0.1) { // Only show significant heat flow
                const arrow = new THREE.Mesh(arrowGeometry, arrowMaterial);

                // Position the arrow
                arrow.position.set(
                    vector.position[0],
                    vector.position[1],
                    vector.position[2]
                );

                // Orient the arrow in the direction of heat flow
                const direction = new THREE.Vector3(
                    vector.direction[0],
                    vector.direction[1],
                    vector.direction[2]
                );
                arrow.lookAt(arrow.position.clone().add(direction));

                // Scale based on magnitude
                const scale = Math.min(vector.magnitude * 0.1, 2.0);
                arrow.scale.setScalar(scale);

                this.scene.add(arrow);
                this.heatVectorObjects.push(arrow);
            }
        });

        this.heatVectors = heatVectors;
    }

    updateHeatSource(temperature) {
        // Update heat source light intensity based on temperature
        if (this.heatSourceLight) {
            const intensity = Math.max(0, (temperature - 400) / 400) * 2.0;
            this.heatSourceLight.intensity = intensity;

            // Change color based on temperature
            if (temperature > 600) {
                this.heatSourceLight.color.setHex(0xff2000);
            } else if (temperature > 400) {
                this.heatSourceLight.color.setHex(0xff6000);
            } else {
                this.heatSourceLight.color.setHex(0xff8040);
            }
        }
    }

    frameObject() {
        if (!this.meshObject) return;

        // Calculate bounding box
        const box = new THREE.Box3().setFromObject(this.meshObject);
        const center = box.getCenter(new THREE.Vector3());
        const size = box.getSize(new THREE.Vector3());

        // Position camera to frame the object
        const maxDim = Math.max(size.x, size.y, size.z);
        const distance = maxDim * 2;

        this.camera.position.set(
            center.x + distance,
            center.y + distance,
            center.z + distance
        );
        this.camera.lookAt(center);
        this.controls.target.copy(center);
        this.controls.update();
    }

    startRenderLoop() {
        const animate = () => {
            this.animationId = requestAnimationFrame(animate);

            this.time += 0.016; // ~60fps

            // Update shader uniforms
            if (this.heatMaterial) {
                this.heatMaterial.uniforms.time.value = this.time;
                this.heatMaterial.uniforms.cameraPosition.value.copy(this.camera.position);
            }

            // Update controls
            this.controls.update();

            // Render
            this.renderer.render(this.scene, this.camera);
        };

        animate();
    }

    onWindowResize() {
        const width = this.container.clientWidth;
        const height = this.container.clientHeight;

        this.camera.aspect = width / height;
        this.camera.updateProjectionMatrix();

        this.renderer.setSize(width, height);
    }

    updateSimulation(data) {
        // Update temperature field
        if (data.temperature_field) {
            this.updateTemperatureField(data.temperature_field);
        }

        // Update heat vectors if available
        if (data.enhanced_viz && data.enhanced_viz.heat_vectors) {
            this.updateHeatVectors(data.enhanced_viz.heat_vectors);
        }

        // Update heat source
        this.updateHeatSource(data.max_temp);

        // Update material properties if available
        if (data.enhanced_viz && data.enhanced_viz.material_properties) {
            this.updateMaterialProperties(data.enhanced_viz.material_properties);
        }
    }

    updateTemperatureField(temperatureField) {
        if (!this.meshObject || !temperatureField) return;

        const geometry = this.meshObject.geometry;
        const tempAttribute = geometry.getAttribute('temperature');

        if (tempAttribute && tempAttribute.array.length === temperatureField.length) {
            // Update temperature values
            for (let i = 0; i < temperatureField.length; i++) {
                tempAttribute.array[i] = temperatureField[i];
            }
            tempAttribute.needsUpdate = true;

            // Update shader uniforms
            if (this.heatMaterial) {
                this.heatMaterial.uniforms.maxTemp.value = Math.max(...temperatureField);
                this.heatMaterial.uniforms.minTemp.value = Math.min(...temperatureField);
            }
        }

        this.temperatureField = temperatureField;
    }

    updateMaterialProperties(materialProps) {
        // This could be used to update material properties dynamically
        // For now, we handle this in the shader
        this.materialProperties = materialProps;
    }

    dispose() {
        if (this.animationId) {
            cancelAnimationFrame(this.animationId);
        }

        if (this.renderer) {
            this.renderer.dispose();
        }

        if (this.meshObject) {
            this.meshObject.geometry.dispose();
            this.meshObject.material.dispose();
        }

        this.heatVectorObjects.forEach(obj => {
            obj.geometry.dispose();
            obj.material.dispose();
        });
    }
}
