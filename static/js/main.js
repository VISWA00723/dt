// Global variables
let scene, camera, renderer, controls;
let meshGroup = new THREE.Group();
let simulationSocket;
let isSimulationRunning = false;
let currentTemperature = 25.0;
let maxTemperature = 25.0;
let minTemperature = 25.0;
let simulationStartTime = 0;
let simulationProgress = 0;
let meshData = null;

// Enhanced renderer
let enhancedRenderer = null;

// Color gradient for temperature visualization
const tempGradient = [
    { temp: 0, color: new THREE.Color(0x0000ff) },    // Blue (cold)
    { temp: 100, color: new THREE.Color(0x00ff00) },  // Green
    { temp: 200, color: new THREE.Color(0xffff00) },  // Yellow
    { temp: 300, color: new THREE.Color(0xff7f00) },  // Orange
    { temp: 400, color: new THREE.Color(0xff0000) },  // Red (hot)
    { temp: 500, color: new THREE.Color(0xff00ff) },  // Magenta (very hot)
    { temp: 600, color: new THREE.Color(0xffffff) }   // White (molten)
];

// Initialize the 3D scene
function initScene() {
    const container = document.getElementById('render-container');

    // Initialize enhanced renderer
    try {
        enhancedRenderer = new EnhancedRenderer(container);
        console.log('Enhanced renderer initialized successfully');

        // Hide loading indicator
        showLoading(false);

        return;
    } catch (error) {
        console.warn('Enhanced renderer failed to initialize, falling back to basic renderer:', error);
        enhancedRenderer = null;
    }

    // Fallback to basic Three.js setup
    console.log('Using fallback basic renderer');

    // Create scene with a dark blue-gray background
    scene = new THREE.Scene();
    scene.background = new THREE.Color(0x1a202c);

    // Create camera with better default position
    camera = new THREE.PerspectiveCamera(45, window.innerWidth / window.innerHeight, 0.1, 10000);
    camera.position.set(2, 2, 2);
    camera.lookAt(0, 0, 0);

    // Create renderer with GPU optimizations
    const rendererConfig = {
        antialias: true,
        powerPreference: 'high-performance',
        alpha: false,
        stencil: false,
        depth: true,
        preserveDrawingBuffer: false,
        failIfMajorPerformanceCaveat: false
    };

    renderer = new THREE.WebGLRenderer(rendererConfig);

    // Set size and enable shadows
    renderer.setSize(
        container.clientWidth,
        container.clientHeight
    );

    // Performance optimizations
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.outputEncoding = THREE.sRGBEncoding;
    renderer.physicallyCorrectLights = true;
    renderer.gammaFactor = 2.2;

    // Enable WebGL 2.0 if available
    if (renderer.capabilities.isWebGL2) {
        console.log('WebGL 2.0 is available');
        renderer.getContext().getExtension('EXT_color_buffer_float');
    }

    // Log WebGL renderer info
    console.log('WebGL Renderer:', renderer.info.render);
    container.appendChild(renderer.domElement);

    // Add orbit controls with full mouse support
    controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.05;
    controls.enableZoom = true;      // Mouse wheel zoom
    controls.enableRotate = true;    // Left-click drag to rotate
    controls.enablePan = true;       // Right-click drag to pan
    controls.autoRotate = false;     // Disable auto-rotation

    // Configure mouse buttons
    controls.mouseButtons = {
        LEFT: THREE.MOUSE.ROTATE,
        MIDDLE: THREE.MOUSE.DOLLY,
        RIGHT: THREE.MOUSE.PAN
    };

    // Configure touch gestures
    controls.touches = {
        ONE: THREE.TOUCH.ROTATE,
        TWO: THREE.TOUCH.DOLLY_PAN
    };

    // Add lights
    const ambientLight = new THREE.AmbientLight(0x404040);
    scene.add(ambientLight);

    // Add directional light
    const directionalLight = new THREE.DirectionalLight(0xffffff, 0.8);
    directionalLight.position.set(1, 1, 1);
    directionalLight.castShadow = true;
    scene.add(directionalLight);

    // Add a point light for better illumination
    const pointLight = new THREE.PointLight(0xffffff, 1, 100);
    pointLight.position.set(5, 5, 5);
    scene.add(pointLight);

    // Add coordinate axes helper (larger size)
    const axesSize = 10;
    const axesHelper = new THREE.AxesHelper(axesSize);
    scene.add(axesHelper);

    // Add a grid helper for better orientation
    const gridHelper = new THREE.GridHelper(20, 20, 0x888888, 0x444444);
    scene.add(gridHelper);

    // Add mesh group to scene with a bounding box helper
    scene.add(meshGroup);

    // Add a bounding box helper for the mesh group
    const bbox = new THREE.Box3().setFromObject(meshGroup);
    const bboxHelper = new THREE.Box3Helper(bbox, new THREE.Color(0xffff00));
    scene.add(bboxHelper);

    // Auto-fit camera to the scene
    function fitCameraToObject(camera, object, offset = 1.5) {
        const box = new THREE.Box3().setFromObject(object);
        const size = box.getSize(new THREE.Vector3());
        const center = box.getCenter(new THREE.Vector3());

        // Calculate the distance to fit the object
        const maxDim = Math.max(size.x, size.y, size.z);
        const fov = camera.fov * (Math.PI / 180);
        let cameraZ = Math.abs(maxDim / Math.sin(fov / 2));

        // Add some padding
        cameraZ *= offset;

        // Position the camera
        camera.position.copy(center);
        camera.position.z += cameraZ;

        // Look at the center of the object
        camera.lookAt(center);

        // Update the camera's projection matrix
        camera.updateProjectionMatrix();
    }

    // Handle window resize
    window.addEventListener('resize', onWindowResize, false);

    // Start animation loop
    animate();
}

// Handle window resize
function onWindowResize() {
    const container = document.getElementById('render-container');
    camera.aspect = container.clientWidth / container.clientHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(container.clientWidth, container.clientHeight);
}

// Track FPS for performance monitoring
let frameCount = 0;
let lastFpsUpdate = 0;
let lastTime = performance.now();
let fps = 0;

// Animation loop with performance optimizations
function animate(currentTime) {
    requestAnimationFrame(animate);

    // Calculate FPS
    frameCount++;
    const time = performance.now();

    // Only update FPS counter once per second
    if (time >= lastFpsUpdate + 1000) {
        const fps = Math.round((frameCount * 1000) / (time - lastFpsUpdate));
        const fpsElement = document.getElementById('fps-counter');
        if (fpsElement) {
            fpsElement.textContent = `${fps} FPS`;
        }
        frameCount = 0;
        lastFpsUpdate = time;
    }

    // Limit FPS to 30 for better performance
    const delta = time - lastTime;
    const targetFPS = 30; // Reduced from 60 to 30 FPS
    const interval = 1000 / targetFPS;

    if (delta < interval) {
        return; // Skip this frame to maintain target FPS
    }

    lastTime = time - (delta % interval);

    // Update controls only if needed
    if (controls && controls.enabled) {
        controls.update();
    }

    // Only render if needed
    if (needsRender) {
        if (enhancedRenderer) {
            enhancedRenderer.render();
        } else if (renderer && scene && camera) {
            // Use a lower resolution when moving for better performance
            const pixelRatio = controls && controls.enabled ? 0.5 : 1.0;
            if (renderer.getPixelRatio() !== pixelRatio) {
                renderer.setPixelRatio(pixelRatio);
                renderer.setSize(renderer.domElement.clientWidth, renderer.domElement.clientHeight, false);
            }

            renderer.render(scene, camera);
        }
        needsRender = false;
    }
}

// Load mesh from server
async function loadMesh() {
    let vertexArray = null;
    let faceCount = 0;

    try {
        showLoading(true);
        logMessage('Loading mesh data from server...');

        const response = await fetch('/api/mesh');
        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            throw new Error(errorData.detail || `HTTP error! status: ${response.status}`);
        }

        const meshData = await response.json();
        console.log('Mesh data received:', meshData);

        // Use enhanced renderer if available
        if (enhancedRenderer) {
            try {
                enhancedRenderer.updateMesh(meshData, meshData.temperature_field || []);
                logMessage('Mesh loaded with enhanced renderer');
                showLoading(false);
                return;
            } catch (error) {
                console.warn('Enhanced renderer failed, falling back to basic renderer:', error);
                enhancedRenderer = null;
            }
        }

        // Validate mesh data with more detailed error checking
        console.log('Raw mesh data received:', meshData);

        if (!meshData.vertices && !meshData.points) {
            throw new Error('No vertex data found in mesh data');
        }

        // Handle different possible data structures
        const vertices = meshData.vertices || meshData.points || [];
        const faces = meshData.faces || meshData.indices || [];

        if (vertices.length === 0) {
            throw new Error('No vertex data available');
        }

        console.log(`Loaded ${vertices.length} vertices and ${faces.length} faces`);

        // Clear existing meshes
        while (meshGroup.children.length > 0) {
            const child = meshGroup.children[0];
            if (child.geometry) child.geometry.dispose();
            if (child.material) {
                if (Array.isArray(child.material)) {
                    child.material.forEach(mat => mat.dispose());
                } else {
                    child.material.dispose();
                }
            }
            meshGroup.remove(child);
        }

        // Create a single buffer geometry for all vertices and faces
        const geometry = new THREE.BufferGeometry();

        // First, validate vertices input
        if (!Array.isArray(vertices) || vertices.length === 0) {
            throw new Error('No vertex data provided');
        }

        // Convert vertices to Float32Array, handling different input formats
        if (Array.isArray(vertices[0]) && vertices[0].length >= 3) {
            // Handle [[x,y,z], [x,y,z], ...] format
            vertexArray = new Float32Array(vertices.flat());
        } else if (vertices.length % 3 === 0 && typeof vertices[0] === 'number') {
            // Handle [x,y,z, x,y,z, ...] format
            vertexArray = new Float32Array(vertices);
        } else {
            throw new Error('Unsupported vertex format. Expected [[x,y,z],...] or [x,y,z,x,y,z,...]');
        }

        // Ensure we have valid vertex data
        if (vertexArray.length === 0 || vertexArray.some(isNaN)) {
            console.error('Invalid vertex data:', vertexArray);
            throw new Error('Vertex data contains invalid values');
        }

        // Set vertex attributes
        geometry.setAttribute('position', new THREE.Float32BufferAttribute(vertexArray, 3));

        // Process faces/indices if available
        if (faces && faces.length > 0) {
            let indexArray;
            // Handle different face array formats
            if (Array.isArray(faces[0])) {
                // Handle [[0,1,2], [1,2,3], ...] format
                indexArray = new Uint32Array(faces.flat());
            } else if (faces.length % 3 === 0) {
                // Handle [0,1,2, 1,2,3, ...] format
                indexArray = new Uint32Array(faces);
            } else {
                console.warn('Unsupported face format, falling back to point cloud');
                throw new Error('Unsupported face format');
            }

            // Only set index if we have valid face data
            if (indexArray.length > 0 && !indexArray.some(isNaN)) {
                geometry.setIndex(new THREE.BufferAttribute(indexArray, 1));
                console.log(`Created mesh with ${vertexArray.length / 3} vertices and ${indexArray.length / 3} faces`);
                faceCount = indexArray.length / 3;
            } else {
                console.warn('No valid face data, falling back to point cloud');
                createPointCloud(geometry, vertexArray);
                return; // Exit early since we're creating a point cloud
            }
        } else {
            console.warn('No face data available, creating point cloud');
            createPointCloud(geometry, vertexArray);
            return; // Exit early since we're creating a point cloud
        }

        // Compute bounding box and center the geometry
        geometry.computeBoundingBox();
        const center = new THREE.Vector3();
        geometry.boundingBox.getCenter(center);
        geometry.translate(-center.x, -center.y, -center.z);

        // Compute normals for proper lighting
        try {
            geometry.computeVertexNormals();
        } catch (e) {
            console.warn('Could not compute vertex normals:', e);
        }

        // Create material with solid color
        const material = new THREE.MeshPhongMaterial({
            color: 0x4fc3f7,                // Light blue color
            emissive: 0x0a3d62,              // Darker blue emissive
            emissiveIntensity: 0.3,          // Slight glow
            specular: 0xffffff,              // White specular highlights
            shininess: 30,                  // Moderate shininess
            side: THREE.DoubleSide,
            flatShading: false,              // Smooth shading
            vertexColors: false,
            transparent: false,
            opacity: 1.0
        });

        // Add wireframe as a separate object for better control
        const wireframe = new THREE.LineSegments(
            new THREE.WireframeGeometry(geometry),
            new THREE.LineBasicMaterial({
                color: 0x000000,            // Black wireframe
                transparent: true,
                opacity: 0.2,               // Subtle wireframe
                linewidth: 1
            })
        );
        wireframe.renderOrder = 1;  // Ensure wireframe is on top

        // Create mesh and add to scene
        const mesh = new THREE.Mesh(geometry, material);
        mesh.castShadow = true;
        mesh.receiveShadow = true;

        // Add wireframe to the mesh
        mesh.add(wireframe);

        // Center the mesh
        geometry.computeBoundingBox();
        const center2 = new THREE.Vector3();
        geometry.boundingBox.getCenter(center2);
        mesh.geometry.translate(-center2.x, -center2.y, -center2.z);

        meshGroup.add(mesh);

        // Adjust camera to fit the mesh with better zoom
        const box = new THREE.Box3().setFromObject(meshGroup);
        const size = box.getSize(new THREE.Vector3()).length();
        const center3 = box.getCenter(new THREE.Vector3());

        // Position camera closer to the model
        camera.position.copy(center3);
        camera.position.x += size * 0.8;  // Closer than before (was 1.5)
        camera.position.y += size * 0.4;  // Slightly lower angle (was 0.5)
        camera.position.z += size * 0.8;  // Closer than before (was 1.5)
        camera.lookAt(center3);

        // Update camera settings for better zoom
        camera.near = 0.1;  // Closer near plane for better zoom
        camera.far = 10000; // Far plane to see everything
        camera.updateProjectionMatrix();

        // Configure controls for better zooming
        controls.target.copy(center3);
        controls.enableDamping = true;  // Smooth camera movement
        controls.dampingFactor = 0.05;  // Smoother movement
        controls.minDistance = size * 0.5;  // Can zoom in closer
        controls.maxDistance = size * 5;    // Can zoom out further
        controls.update();

        const vertexCount = vertexArray ? vertexArray.length / 3 : 0;
        logMessage(`Mesh loaded successfully (${vertexCount} vertices, ${faceCount} faces)`);

        // Display mesh properties
        displayMeshProperties(meshData);

        // Add toggle for wireframe
        const toggleWireframe = () => {
            mesh.material.wireframe = !mesh.material.wireframe;
        };

        // Add button to toggle wireframe
        const wireframeBtn = document.getElementById('toggle-wireframe');
        if (wireframeBtn) {
            wireframeBtn.onclick = toggleWireframe;
            wireframeBtn.style.display = 'inline-block';
        }

    } catch (error) {
        console.error('Error loading mesh:', error);
        const errorMessage = error instanceof Error ? error.message : 'Unknown error occurred';
        logMessage(`Error: ${errorMessage}`, 'error');

        // Add a simple cube to show that 3D is working
        const geometry = new THREE.BoxGeometry(1, 1, 1);
        const material = new THREE.MeshBasicMaterial({ color: 0xff0000, wireframe: true });
        const cube = new THREE.Mesh(geometry, material);
        meshGroup.add(cube);

        // Add error message to the scene
        const errorText = new THREE.TextGeometry('Mesh Load Error', {
            size: 0.5,
            height: 0.1
        });
        const textMaterial = new THREE.MeshBasicMaterial({ color: 0xff0000 });
        const textMesh = new THREE.Mesh(errorText, textMaterial);
        textMesh.position.set(-2, 0, 0);
        meshGroup.add(textMesh);
    } finally {
        showLoading(false);
    }
}

// Create a point cloud from vertex data
function createPointCloud(geometry, vertexArray) {
    const pointCount = vertexArray.length / 3;
    console.log(`Creating point cloud with ${pointCount.toLocaleString()} points`);

    // For very large point clouds, adjust the size and other parameters
    const isLargePointCloud = pointCount > 10000;
    const pointSize = isLargePointCloud ? 1.5 : 1.0;

    // Create optimized points material
    const material = new THREE.PointsMaterial({
        color: 0x2194ce,
        size: pointSize,
        vertexColors: true,
        transparent: true,
        opacity: isLargePointCloud ? 0.8 : 0.9,
        sizeAttenuation: true,
        alphaTest: 0.1
    });

    // For very large point clouds, log a warning
    if (pointCount > 100000) {
        console.warn(`Large point cloud detected (${pointCount.toLocaleString()} points). Consider simplifying the mesh.`);
    }

    // Create points geometry
    const pointsGeometry = new THREE.BufferGeometry();
    pointsGeometry.setAttribute('position', new THREE.Float32BufferAttribute(vertexArray, 3));

    // Create points and add to scene
    const points = new THREE.Points(pointsGeometry, material);
    meshGroup.add(points);

    // Adjust camera to fit the point cloud
    const box = new THREE.Box3().setFromObject(meshGroup);
    const size = box.getSize(new THREE.Vector3()).length();
    const center = box.getCenter(new THREE.Vector3());

    camera.position.copy(center);
    camera.position.x += size * 1.5;
    camera.position.y += size * 0.5;
    camera.position.z += size * 1.5;
    camera.lookAt(center);

    controls.target.copy(center);
    controls.update();

    logMessage(`Created point cloud with ${vertexArray.length / 3} points`);
    return points;
}

// Helper function to create a property table for a single part
function createPartPropertyTable(part, partName) {
    if (!part) return null;

    const container = document.createElement('div');
    container.className = 'space-y-6';

    // Material properties section
    const materialSection = document.createElement('div');
    materialSection.className = 'glass-panel rounded-lg p-4';
    materialSection.innerHTML = `
        <h3 class="text-xs font-bold text-cyan-400 uppercase tracking-wider mb-3">Material Properties</h3>
        <table class="w-full text-xs">
            <tbody>
                <tr class="hover:bg-slate-800/50">
                    <td class="py-1.5 pr-4 text-slate-400">Alloy:</td>
                    <td class="py-1.5 font-mono text-slate-200">${part.alloy || 'Unknown'}</td>
                </tr>
                <tr class="hover:bg-slate-800/50">
                    <td class="py-1.5 pr-4 text-slate-400">Density:</td>
                    <td class="py-1.5 font-mono text-slate-200">${part.density ? part.density.toFixed(0) + ' kg/m³' : 'N/A'}</td>
                </tr>
                <tr class="hover:bg-slate-800/50">
                    <td class="py-1.5 pr-4 text-slate-400">Thermal Conductivity:</td>
                    <td class="py-1.5 font-mono text-slate-200">${part.thermal_conductivity ? part.thermal_conductivity.toFixed(0) + ' W/(m·K)' : 'N/A'}</td>
                </tr>
                <tr class="hover:bg-slate-800/50">
                    <td class="py-1.5 pr-4 text-slate-400">Specific Heat:</td>
                    <td class="py-1.5 font-mono text-slate-200">${part.specific_heat ? part.specific_heat.toFixed(0) + ' J/(kg·K)' : 'N/A'}</td>
                </tr>
                <tr class="hover:bg-slate-800/50">
                    <td class="py-1.5 pr-4 text-slate-400">Solidus:</td>
                    <td class="py-1.5 font-mono text-slate-200">${part.solidus ? part.solidus.toFixed(0) + '°C' : 'N/A'}</td>
                </tr>
                <tr class="hover:bg-slate-800/50">
                    <td class="py-1.5 pr-4 text-slate-400">Liquidus:</td>
                    <td class="py-1.5 font-mono text-slate-200">${part.liquidus ? part.liquidus.toFixed(0) + '°C' : 'N/A'}</td>
                </tr>
            </tbody>
        </table>
    `;

    // Geometric properties section
    const geometrySection = document.createElement('div');
    geometrySection.className = 'glass-panel rounded-lg p-4';

    // Format dimensions
    let dimensions = 'N/A';
    if (part.extents && part.extents.length >= 3) {
        const [l, w, h] = part.extents.map(d => d.toFixed(2));
        dimensions = `${l} × ${w} × ${h} mm`;
    }

    // Format centroid
    let centroid = 'N/A';
    if (part.centroid && part.centroid.length >= 3) {
        const [x, y, z] = part.centroid.map(d => d.toFixed(2));
        centroid = `(${x}, ${y}, ${z}) mm`;
    }

    // Get surface area (prefer convex_hull_area if available)
    const area = part.convex_hull_area || part.surface_area;

    geometrySection.innerHTML = `
        <h3 class="text-xs font-bold text-cyan-400 uppercase tracking-wider mb-3">Geometric Properties</h3>
        <table class="w-full text-xs">
            <tbody>
                <tr class="hover:bg-slate-800/50">
                    <td class="py-1.5 pr-4 text-slate-400">Vertices:</td>
                    <td class="py-1.5 font-mono text-slate-200">${part.vertex_count ? part.vertex_count.toLocaleString() : '0'}</td>
                </tr>
                <tr class="hover:bg-slate-800/50">
                    <td class="py-1.5 pr-4 text-slate-400">Faces:</td>
                    <td class="py-1.5 font-mono text-slate-200">${part.face_count != null ? part.face_count.toLocaleString() : '0'}</td>
                </tr>
                <tr class="hover:bg-slate-800/50">
                    <td class="py-1.5 pr-4 text-slate-400">Volume:</td>
                    <td class="py-1.5 font-mono text-slate-200">${part.volume ? part.volume.toFixed(2) + ' mm³' : 'N/A'}</td>
                </tr>
                <tr class="hover:bg-slate-800/50">
                    <td class="py-1.5 pr-4 text-slate-400">Surface Area:</td>
                    <td class="py-1.5 font-mono text-slate-200">${area != null ? area.toFixed(2) + ' mm²' : 'N/A'}</td>
                </tr>
                <tr class="hover:bg-slate-800/50">
                    <td class="py-1.5 pr-4 text-slate-400">Dimensions:</td>
                    <td class="py-1.5 font-mono text-slate-200">${dimensions}</td>
                </tr>
                <tr class="hover:bg-slate-800/50">
                    <td class="py-1.5 pr-4 text-slate-400">Centroid:</td>
                    <td class="py-1.5 font-mono text-slate-200">${centroid}</td>
                </tr>
            </tbody>
        </table>
    `;

    container.appendChild(materialSection);
    container.appendChild(geometrySection);
    return container;
}

// Function to show properties for a specific part
function showPartProperties(partName) {
    // Hide all property sections first
    document.querySelectorAll('.part-properties').forEach(el => {
        el.classList.add('hidden');
    });

    // Show the selected part's properties
    const selectedPart = document.getElementById(`${partName}-properties`);
    if (selectedPart) {
        selectedPart.classList.remove('hidden');
    }

    // Update active button state
    document.querySelectorAll('.property-btn').forEach(btn => {
        if (btn.dataset.part === partName) {
            btn.classList.add('border-cyan-400', 'text-cyan-400', 'bg-slate-800/50');
        } else {
            btn.classList.remove('border-cyan-400', 'text-cyan-400', 'bg-slate-800/50');
        }
    });
}

// Display mesh properties in the UI
function displayMeshProperties(meshData) {
    const propertiesContainer = document.getElementById('mesh-properties');
    if (!propertiesContainer) return;

    // Show the properties container
    propertiesContainer.classList.remove('hidden');

    // Get the part containers
    const topContainer = document.getElementById('top-properties');
    const fillerContainer = document.getElementById('filler-properties');
    const bottomContainer = document.getElementById('bottom-properties');
    const fixtureContainer = document.getElementById('fixture-properties');

    // Clear previous content
    if (topContainer) topContainer.innerHTML = '';
    if (fillerContainer) fillerContainer.innerHTML = '';
    if (bottomContainer) bottomContainer.innerHTML = '';
    if (fixtureContainer) fixtureContainer.innerHTML = '';

    // Create and append property tables for each part
    if (meshData.top_plate && topContainer) {
        const topTable = createPartPropertyTable(meshData.top_plate, 'top_plate');
        if (topTable) topContainer.appendChild(topTable);
    }

    if (meshData.filler && fillerContainer) {
        const fillerTable = createPartPropertyTable(meshData.filler, 'filler');
        if (fillerTable) fillerContainer.appendChild(fillerTable);
    }

    if (meshData.bottom_plate && bottomContainer) {
        const bottomTable = createPartPropertyTable(meshData.bottom_plate, 'bottom_plate');
        if (bottomTable) bottomContainer.appendChild(bottomTable);
    }

    if (meshData.fixture && fixtureContainer) {
        const fixtureTable = createPartPropertyTable(meshData.fixture, 'fixture');
        if (fixtureTable) fixtureContainer.appendChild(fixtureTable);
    }

    // Add click handlers for property buttons
    document.querySelectorAll('.property-btn').forEach(button => {
        button.addEventListener('click', (e) => {
            e.preventDefault();
            const partName = button.dataset.part;
            showPartProperties(partName);
        });
    });

    // Add click handlers for part tabs in geometry panel
    document.querySelectorAll('.part-tab').forEach(button => {
        button.addEventListener('click', (e) => {
            e.preventDefault();
            const partName = button.dataset.part;

            // Update active tab styling
            document.querySelectorAll('.part-tab').forEach(btn => {
                btn.classList.remove('active', 'border-cyan-400', 'text-cyan-400');
                btn.classList.add('border-slate-600', 'text-slate-300');
            });
            button.classList.add('border-cyan-400', 'text-cyan-400');
            button.classList.remove('border-slate-600', 'text-slate-300');

            // Show the selected part properties
            document.querySelectorAll('.part-properties').forEach(section => {
                section.classList.add('hidden');
            });
            const selectedSection = document.getElementById(`${partName}-properties`);
            if (selectedSection) {
                selectedSection.classList.remove('hidden');
            }
        });
    });

    // Show top properties by default if available, otherwise show the first available part
    if (meshData.top_plate) {
        showPartProperties('top');
    } else if (meshData.filler) {
        showPartProperties('filler');
    } else if (meshData.bottom_plate) {
        showPartProperties('bottom');
    } else if (meshData.fixture) {
        showPartProperties('fixture');
    }
}

// Update mesh colors based on temperature
function updateTemperatureColors(temperatureField) {
    if (!meshGroup.children.length || !temperatureField || temperatureField.length === 0) {
        console.log('No mesh or temperature field to update');
        return;
    }

    const mesh = meshGroup.children[0];
    if (!mesh || !mesh.geometry) {
        console.log('Invalid mesh object');
        return;
    }

    const geometry = mesh.geometry;
    const positions = geometry.attributes.position;

    if (!positions) {
        console.log('No position attribute in geometry');
        return;
    }

    // Create color attribute if it doesn't exist
    if (!geometry.attributes.color) {
        const colors = [];
        for (let i = 0; i < positions.count; i++) {
            colors.push(0, 0, 1); // Default to blue
        }
        geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
    }

    // Ensure temperature field matches vertex count
    const vertexCount = Math.min(positions.count, temperatureField.length);

    // Update colors based on temperature
    const colorAttribute = geometry.attributes.color;

    for (let i = 0; i < vertexCount; i++) {
        const temp = temperatureField[i];
        const color = getColorForTemperature(temp);

        colorAttribute.setXYZ(i, color.r, color.g, color.b);
    }

    colorAttribute.needsUpdate = true;

    // Enable vertex colors on material
    if (mesh.material) {
        mesh.material.vertexColors = true;
        mesh.material.needsUpdate = true;
    }

    // Force render update
    needsRender = true;
}

// Get color for a given temperature
function getColorForTemperature(temp) {
    // Find the two closest colors in the gradient
    for (let i = 0; i < tempGradient.length - 1; i++) {
        if (temp >= tempGradient[i].temp && temp <= tempGradient[i + 1].temp) {
            const t = (temp - tempGradient[i].temp) / (tempGradient[i + 1].temp - tempGradient[i].temp);
            return tempGradient[i].color.clone().lerp(tempGradient[i + 1].color, t);
        }
    }

    // Return the first or last color if temperature is out of range
    return tempGradient[temp < tempGradient[0].temp ? 0 : tempGradient.length - 1].color.clone();
}

// WebSocket connection management
let socketReconnectAttempts = 0;
const MAX_RECONNECT_ATTEMPTS = 5;
const RECONNECT_DELAY = 2000; // 2 seconds
let reconnectTimeout = null;

// Connect to WebSocket for real-time updates
function connectWebSocket() {
    if (simulationSocket) {
        console.log('Closing existing WebSocket connection');
        simulationSocket.onclose = null; // Prevent duplicate close handlers
        simulationSocket.close();
    }

    // Clear any pending reconnection
    if (reconnectTimeout) {
        clearTimeout(reconnectTimeout);
        reconnectTimeout = null;
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/sim`;

    console.log(`Connecting to WebSocket at ${wsUrl}...`);
    logMessage('Connecting to simulation server...');

    try {
        simulationSocket = new WebSocket(wsUrl);

        simulationSocket.onopen = () => {
            console.log('WebSocket connection established');
            socketReconnectAttempts = 0; // Reset reconnect attempts on successful connection
            isSimulationRunning = true;
            simulationStartTime = Date.now();
            const statusElement = document.getElementById('simulation-status');
            if (statusElement) {
                statusElement.textContent = 'Simulation running...';
                statusElement.className = 'text-green-600';
            }
            logMessage('Connected to simulation server', 'success');
        };

        simulationSocket.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                console.debug('WebSocket message received:', data);

                // Handle error messages
                if (data.error) {
                    logMessage(`Simulation Error: ${data.error}`, 'error');
                    return;
                }

                // Update temperature statistics
                if (data.max_temp !== undefined) {
                    maxTemperature = Math.max(maxTemperature, data.max_temp);
                    minTemperature = Math.min(minTemperature, data.min_temp);
                    currentTemperature = data.avg_temp || data.max_temp;

                    const tempDisplay = document.getElementById('temperature-display');
                    if (tempDisplay) {
                        tempDisplay.innerHTML = `
                            <div class="text-sm">
                                <div>Max: <span class="font-bold text-red-600">${data.max_temp.toFixed(1)}°C</span></div>
                                <div>Min: <span class="font-bold text-blue-600">${data.min_temp.toFixed(1)}°C</span></div>
                                <div>Avg: <span class="font-bold text-orange-600">${data.avg_temp.toFixed(1)}°C</span></div>
                            </div>
                        `;
                    }
                }

                // Update progress bar
                if (data.progress !== undefined) {
                    simulationProgress = data.progress;
                    const progressFill = document.getElementById('progress-fill');
                    const progressText = document.getElementById('progress-text');
                    const timeDisplay = document.getElementById('simulation-time');

                    if (progressFill) progressFill.style.width = `${simulationProgress}%`;
                    if (progressText) progressText.textContent = `${Math.round(simulationProgress)}%`;
                    if (timeDisplay && data.time !== undefined) {
                        timeDisplay.textContent = `Time: ${data.time.toFixed(1)}s`;
                    }
                }

                // Update visualization with temperature field and enhanced data
                if (data.temperature_field && data.temperature_field.length > 0) {
                    if (enhancedRenderer) {
                        // Use enhanced renderer for realistic visualization
                        enhancedRenderer.updateSimulation(data);
                    } else {
                        // Fallback to basic temperature color update
                        updateTemperatureColors(data.temperature_field);
                    }
                }

                // Log simulation frame info
                if (data.frame !== undefined) {
                    console.debug(`Frame ${data.frame}: Time=${data.time.toFixed(2)}s, Progress=${data.progress.toFixed(1)}%, Max=${data.max_temp.toFixed(1)}°C`);
                }

                // Check if simulation is complete
                if (data.type === 'simulation_complete' || data.simulation_complete) {
                    isSimulationRunning = false;
                    const statusElement = document.getElementById('simulation-status');

                    if (statusElement) {
                        statusElement.textContent = 'Simulation complete!';
                        statusElement.className = 'text-green-600';
                    }
                    logMessage(`Simulation completed! Final Max Temp: ${data.max_temp.toFixed(1)}°C`, 'success');
                }

            } catch (error) {
                console.error('Error processing WebSocket message:', error);
                logMessage(`Error processing simulation data: ${error.message}`, 'error');
            }
        };

        simulationSocket.onclose = (event) => {
            console.log(`WebSocket closed: code=${event.code}, reason=${event.reason || 'No reason provided'}`);

            if (isSimulationRunning) {
                const statusElement = document.getElementById('simulation-status');
                if (statusElement) {
                    statusElement.textContent = 'Connection lost, reconnecting...';
                    statusElement.className = 'text-yellow-600';
                }

                // Attempt to reconnect with exponential backoff
                if (socketReconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
                    const delay = RECONNECT_DELAY * Math.pow(2, socketReconnectAttempts);
                    logMessage(`Connection lost. Attempting to reconnect (${socketReconnectAttempts + 1}/${MAX_RECONNECT_ATTEMPTS})...`, 'warning');

                    reconnectTimeout = setTimeout(() => {
                        socketReconnectAttempts++;
                        connectWebSocket();
                    }, delay);
                } else {
                    logMessage('Max reconnection attempts reached. Please refresh the page to try again.', 'error');
                    isSimulationRunning = false;

                    const statusElement = document.getElementById('simulation-status');
                    if (statusElement) {
                        statusElement.textContent = 'Connection failed';
                        statusElement.className = 'text-red-600';
                    }
                }
            } else {
                console.log('WebSocket closed normally');
            }
        };

        simulationSocket.onerror = (error) => {
            console.error('WebSocket error:', error);
            logMessage('Error in simulation connection', 'error');
            isSimulationRunning = false;

            const statusElement = document.getElementById('simulation-status');
            if (statusElement) {
                statusElement.textContent = 'Connection error';
                statusElement.className = 'text-red-600';
            }
        };

    } catch (error) {
        console.error('Failed to create WebSocket:', error);
        logMessage(`Failed to connect to simulation server: ${error.message}`, 'error');

        // Schedule reconnection attempt
        if (socketReconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
            const delay = RECONNECT_DELAY * Math.pow(2, socketReconnectAttempts);
            logMessage(`Connection failed. Retrying in ${delay / 1000} seconds... (${socketReconnectAttempts + 1}/${MAX_RECONNECT_ATTEMPTS})`, 'warning');

            reconnectTimeout = setTimeout(() => {
                socketReconnectAttempts++;
                connectWebSocket();
            }, delay);
        } else {
            logMessage('Max reconnection attempts reached. Please refresh the page to try again.', 'error');
        }
    }
}

// Upload files to server
async function uploadFiles() {
    const formData = new FormData();
    const topPlate = document.getElementById('topPlate').files[0];
    const filler = document.getElementById('filler').files[0];
    const bottomPlate = document.getElementById('bottomPlate').files[0];
    const fixture = document.getElementById('fixture').files[0];
    const fillerType = document.getElementById('fillerType').value;
    const carbonSheetThickness = parseFloat(document.getElementById('carbonSheetThickness').value);

    // Validate files
    if (!topPlate || !filler || !bottomPlate) {
        const missingFiles = [];
        if (!topPlate) missingFiles.push('Top Plate');
        if (!filler) missingFiles.push('Filler');
        if (!bottomPlate) missingFiles.push('Bottom Plate');

        logMessage(`Error: Please select files for: ${missingFiles.join(', ')}`, 'error');
        return;
    }

    // Add files to form data
    formData.append('top_plate', topPlate);
    formData.append('filler', filler);
    formData.append('bottom_plate', bottomPlate);
    if (fixture) {
        formData.append('fixture', fixture);
    }
    formData.append('filler_type', fillerType);
    formData.append('carbon_sheet_thickness_mm', carbonSheetThickness);

    logMessage(`Uploading files with filler material: ${fillerType}, carbon sheet: ${carbonSheetThickness}mm...`);


    try {
        showLoading(true);
        logMessage('Uploading files...');

        const response = await fetch('/upload', {
            method: 'POST',
            body: formData
        });

        if (!response.ok) {
            throw new Error(`Server returned ${response.status}: ${response.statusText}`);
        }

        const data = await response.json();
        logMessage('Files uploaded and processed successfully');

        // Update material properties display
        updateMaterialProperties(data.meshes, data.melting_analysis);

        // Load the mesh for visualization
        await loadMesh();

        // Enable simulation controls
        document.getElementById('simulationParams').style.opacity = 1;
        document.getElementById('simulationParams').style.opacity = 1;
        document.getElementById('simulationParams').style.pointerEvents = 'auto';

        // Show cycle settings panel
        const cycleSettingsPanel = document.getElementById('cycleSettingsPanel');
        if (cycleSettingsPanel) {
            cycleSettingsPanel.style.display = 'block';
            cycleSettingsPanel.classList.add('fade-in');
        }

        logMessage('Ready to start simulation');

        // Fetch and display brazing cycle
        await getBrazingCycle();

    } catch (error) {
        console.error('Error uploading files:', error);
        logMessage(`Error: ${error.message}`, 'error');
    } finally {
        showLoading(false);
    }
}

// Helper to scrape table data
function scrapeCycleTable() {
    const tbody = document.getElementById('brazingCycleBody');
    if (!tbody || tbody.children.length === 0) return null;

    const stages = [];
    const rows = Array.from(tbody.querySelectorAll('tr'));

    for (const row of rows) {
        const cells = row.querySelectorAll('td');
        if (cells.length < 7) continue;

        // Helper to get value from text or input
        const getVal = (cell) => {
            const input = cell.querySelector('input');
            const text = cell.innerText.replace('°C/min', '').replace('°C', '').replace('min', '').trim();
            const val = input ? input.value : text;
            return val;
        };

        const stageName = cells[0].innerText.trim();
        const temp = parseFloat(getVal(cells[1]));
        const rate = parseFloat(getVal(cells[4]));
        const hold = parseFloat(getVal(cells[5]));
        const purpose = getVal(cells[6]);

        if (!isNaN(temp)) {
            stages.push({
                "Stage": stageName,
                "Temperature": temp,
                "RampRate": isNaN(rate) ? 0 : rate,
                "HoldTime": isNaN(hold) ? 0 : hold,
                "Purpose": purpose
            });
        }
    }
    return stages.length > 0 ? stages : null;
}

// Fetch brazing cycle parameters from server
async function getBrazingCycle() {
    try {
        const stage1TempInput = document.getElementById('stage1Temp');
        const stage1RampInput = document.getElementById('stage1Ramp');

        // Check if we have existing table data to serve as overrides
        const existingData = scrapeCycleTable();

        let url = '/simulation-parameters';
        let method = 'GET';
        let body = null;
        let headers = {};

        // DECISION LOGIC:
        // 1. If we have table data (User Edits) -> Use POST with full cycle
        // 2. If table is empty (First Run) -> Use GET with query params

        if (existingData && existingData.length > 0) {
            logMessage('Recalculating cycle based on USER EDITS (Custom Physics)...');
            method = 'POST';
            headers = { 'Content-Type': 'application/json' };
            body = JSON.stringify({ stages: existingData });

            // Note: We ignore the top-level Stage 1 inputs here because the table 
            // already contains the user's specific Stage 1 values derived from them or edited.
        } else {
            logMessage('Calculating initial physics-based brazing cycle...');
            const params = new URLSearchParams();
            if (stage1TempInput && stage1TempInput.value) params.append('initial_temp', stage1TempInput.value);
            if (stage1RampInput && stage1RampInput.value) params.append('initial_ramp', stage1RampInput.value);

            const queryString = params.toString();
            if (queryString) {
                url += `?${queryString}`;
                logMessage(`Applying overrides: Temp=${stage1TempInput.value}°C, Ramp=${stage1RampInput.value}°C/min`);
            }
        }

        const response = await fetch(url, { method, headers, body });

        if (!response.ok) {
            throw new Error(`Server returned ${response.status}`);
        }

        const data = await response.json();

        if (data.status === 'success' || data.status === 'warning') {
            if (data.status === 'warning') {
                logMessage(data.message, 'warning');
            } else {
                logMessage('Physics-based cycle generated.');
            }
            renderBrazingCycle(data.parameters);

            // If we were in edit mode, restore it visually?
            // Actually, re-rendering destroys the inputs. 
            // Let's exit edit mode to show the clean calculated results.
            if (window.isEditMode) {
                toggleEditMode(); // Toggle off
            }

            logMessage('Cycle calculation complete.');
        } else {
            throw new Error('Failed to generate parameters');
        }

    } catch (error) {
        console.error('Error fetching brazing cycle:', error);
        logMessage(`Error generating brazing cycle: ${error.message}`, 'error');
    }
}

// Global Edit Mode State
window.isEditMode = false;

// Toggle Edit Mode
function toggleEditMode() {
    window.isEditMode = !window.isEditMode;
    const btn = document.getElementById('editCycleBtn');
    if (btn) {
        btn.textContent = window.isEditMode ? 'Exit Edit Mode' : 'Edit Cycle';
        btn.classList.toggle('bg-amber-600', window.isEditMode);
        btn.classList.toggle('bg-slate-600', !window.isEditMode);
    }

    // re-render table with edit state
    // We need to store latest params globally or scrape them
    // For now, let's just make the EXISTING cells editable inline without re-rendering everything if possible,
    // OR, better, re-scrape current table data and re-render it.

    // Simplest: Just iterate rows and toggle content
    const tbody = document.getElementById('brazingCycleBody');
    if (!tbody) return;

    const rows = Array.from(tbody.querySelectorAll('tr'));
    rows.forEach(row => {
        const cells = row.querySelectorAll('td');
        // Indices: 0=Stage, 1=Temp, 2=Job1(Skip), 3=Job2(Skip), 4=Rate, 5=Hold, 6=Purpose

        const editableIndices = [1, 4, 5, 6];

        editableIndices.forEach(index => {
            const cell = cells[index];
            if (window.isEditMode) {
                // Convert text to input
                const currentText = cell.innerText.replace('°C', '').replace('/min', '').replace('min', '').trim();
                let width = 'w-16';
                if (index === 6) width = 'w-full';

                cell.innerHTML = `<input type="text" class="bg-slate-900 border border-slate-600 text-white text-center rounded px-1 ${width}" value="${currentText}">`;
            } else {
                // Convert input back to text
                const input = cell.querySelector('input');
                if (input) {
                    let val = input.value;
                    // Add units back
                    if (index === 1 || index === 2 || index === 3) val += ''; // Temps usually have °C in render, but we removed it. 
                    // renderBrazingCycle adds units via span, so we might lose that formatting if we just dump text.
                    // Ideally check index 1 is Temp, 4 is Rate, 5 is Hold.

                    // Simple hack: Just set text. The unit spans are lost but it's "Edit Mode".
                    // To restore pretty formatting, we'd need to re-call renderBrazingCycle with updated data object.

                    cell.textContent = val;
                }
            }
        });
    });
}

// Render brazing cycle table with dynamic stages and Final Brazing highlighting
function renderBrazingCycle(parameters) {
    const container = document.getElementById('brazingCycleContainer');
    const tbody = document.getElementById('brazingCycleBody');

    if (!container || !tbody) return;

    tbody.innerHTML = '';

    // Add machine limits info above the table
    const machineInfoDiv = document.createElement('div');
    machineInfoDiv.className = 'mb-3 p-2 bg-slate-900/50 border border-slate-700 rounded text-xs text-slate-300 font-mono';
    machineInfoDiv.innerHTML = `<strong>Machine Limits:</strong> 400–650°C | 1–10°C/min | 14 mbar vacuum`;

    if (tbody.parentElement && !tbody.parentElement.querySelector('.machine-limits-info')) {
        const tableContainer = tbody.closest('.overflow-auto') || tbody.parentElement;
        tableContainer.insertBefore(machineInfoDiv, tableContainer.firstChild);
        machineInfoDiv.classList.add('machine-limits-info');
    }

    parameters.forEach(stage => {
        const row = document.createElement('tr');

        // Highlight Final Brazing stage with amber border and bold text
        const isFinalBrazing = stage.Stage && stage.Stage.toLowerCase().includes('final brazing');
        if (isFinalBrazing) {
            row.className = 'border-l-4 border-amber-500 bg-amber-500/10 hover:bg-amber-500/20 transition-colors';
        } else {
            row.className = 'hover:bg-slate-800/50 transition-colors';
        }

        const temp = typeof stage.Temperature === 'number' ? stage.Temperature.toFixed(0) : stage.Temperature;
        const rate = typeof stage.RampRate === 'number' ? stage.RampRate.toFixed(0) : stage.RampRate;
        const hold = typeof stage.HoldTime === 'number' ? stage.HoldTime.toFixed(0) : stage.HoldTime;

        // Format job temperatures with fallback
        const job1Temp = typeof stage.Job1Temp === 'number' ? stage.Job1Temp.toFixed(1) : '—';
        const job2Temp = typeof stage.Job2Temp === 'number' ? stage.Job2Temp.toFixed(1) : '—';

        // Format stage name and add tooltip for Final Brazing
        let stageName = stage.Stage || 'Unknown';
        let stageHTML = `<span class="${isFinalBrazing ? 'font-bold text-amber-400' : 'text-cyan-400'}">${stageName}</span>`;
        if (isFinalBrazing) {
            stageHTML = `<span class="font-bold text-amber-400" title="Critical liquidus soak stage">${stageName} ⚠</span>`;
        }

        row.innerHTML = `
            <td class="border-b border-slate-800 px-2 py-2 font-medium whitespace-nowrap">${stageHTML}</td>
            <td class="border-b border-slate-800 px-2 py-2 font-mono ${isFinalBrazing ? 'text-amber-400 font-bold' : 'text-amber-400'} text-center">${temp}<span class="text-slate-500 text-[10px] ml-1">°C</span></td>
            <td class="border-b border-slate-800 px-2 py-2 font-mono ${isFinalBrazing ? 'text-cyan-300 font-bold' : 'text-cyan-300'} text-center">${job1Temp}<span class="text-slate-500 text-[10px] ml-1">°C</span></td>
            <td class="border-b border-slate-800 px-2 py-2 font-mono ${isFinalBrazing ? 'text-blue-300 font-bold' : 'text-blue-300'} text-center">${job2Temp}<span class="text-slate-500 text-[10px] ml-1">°C</span></td>
            <td class="border-b border-slate-800 px-2 py-2 font-mono ${isFinalBrazing ? 'text-emerald-400 font-bold' : 'text-emerald-400'} text-center">${rate}<span class="text-slate-500 text-[10px] ml-1">°C/min</span></td>
            <td class="border-b border-slate-800 px-2 py-2 font-mono ${isFinalBrazing ? 'text-blue-400 font-bold' : 'text-blue-400'} text-center">${hold}<span class="text-slate-500 text-[10px] ml-1">min</span></td>
            <td class="border-b border-slate-800 px-2 py-2 text-slate-400 italic text-xs align-top" style="white-space: normal; max-width: 250px;">${stage.Purpose}</td>
        `;

        tbody.appendChild(row);
    });

    container.style.display = 'block';
}

// Update material properties display
// Store materials and melting analysis data globally for tab switching
let currentMaterials = null;
let currentMeltingAnalysis = null;

function generatePropertyHTML(partKey, materials, meltingAnalysis) {
    // Handle fixture material (SS316L)
    if (partKey === 'fixture') {
        const fixtureProps = MATERIAL_PROPERTIES['SS316L'] || {};
        const fixtureData = materials.fixture;
        const geometricProps = fixtureData?.properties || {};

        // Format geometric properties
        let volumeStr = '0.00';
        if (geometricProps.volume !== undefined && geometricProps.volume !== null) {
            volumeStr = geometricProps.volume.toFixed(2);
        }

        const surfaceArea = geometricProps.surface_area ? geometricProps.surface_area.toFixed(2) : '0.00';
        const vertexCount = geometricProps.vertex_count || '0';
        const faceCount = geometricProps.face_count || '0';

        // Format Dimensions
        let dimensionsStr = 'N/A';
        if (geometricProps.extents && geometricProps.extents.length >= 3) {
            dimensionsStr = geometricProps.extents.map(v => v.toFixed(2)).join(' × ');
        }

        // Format Centroid
        let centroidStr = 'N/A';
        if (geometricProps.centroid && geometricProps.centroid.length >= 3) {
            centroidStr = `(${geometricProps.centroid.map(v => v.toFixed(2)).join(', ')})`;
        }

        let html = `
            <div class="glass-panel p-3 rounded border border-slate-700/50 mb-3">
                <div class="font-bold text-cyan-400 text-sm border-b border-slate-700 pb-2 mb-2">
                    Fixture (SS316L)
                </div>
                <div class="text-xs text-slate-300 mb-2 font-mono">Stainless Steel 316L</div>
                
                <div class="mt-2">
                    <div class="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-1">Material Properties</div>
                    <div class="grid grid-cols-2 gap-x-2 gap-y-1 text-xs font-mono">
                        <div class="text-slate-400">Density:</div><div class="text-slate-200 text-right">${fixtureProps.density || 'N/A'} <span class="text-slate-600">kg/m³</span></div>
                        <div class="text-slate-400">Therm. Cond:</div><div class="text-slate-200 text-right">${fixtureProps.thermal_conductivity || 'N/A'} <span class="text-slate-600">W/mK</span></div>
                        <div class="text-slate-400">Spec. Heat:</div><div class="text-slate-200 text-right">${fixtureProps.specific_heat || 'N/A'} <span class="text-slate-600">J/kgK</span></div>
                        <div class="text-slate-400">Solidus:</div><div class="text-slate-200 text-right">${fixtureProps.solidus || 'N/A'}<span class="text-slate-600">°C</span></div>
                        <div class="text-slate-400">Liquidus:</div><div class="text-slate-200 text-right">${fixtureProps.liquidus || 'N/A'}<span class="text-slate-600">°C</span></div>
                    </div>
                </div>
                
                <div class="mt-3 pt-2 border-t border-slate-800">
                    <div class="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-1">Geometric Analysis</div>
                    <div class="grid grid-cols-2 gap-x-2 gap-y-1 text-xs font-mono">
                        <div class="text-slate-400">Volume:</div><div class="text-emerald-400 text-right">${volumeStr} <span class="text-slate-600">mm³</span></div>
                        <div class="text-slate-400">Area:</div><div class="text-blue-400 text-right">${surfaceArea} <span class="text-slate-600">mm²</span></div>
                        <div class="text-slate-400">Vertices:</div><div class="text-slate-200 text-right">${vertexCount}</div>
                        <div class="text-slate-400">Faces:</div><div class="text-slate-200 text-right">${faceCount}</div>
                        <div class="text-slate-400 col-span-2 mt-1">Dimensions:<span class="text-slate-200 float-right">${dimensionsStr}</span></div>
                        <div class="text-slate-400 col-span-2">Centroid:<span class="text-slate-500 float-right text-[10px]">${centroidStr}</span></div>
                    </div>
                </div>
                
                <div class="mt-3 pt-2 border-t border-slate-800">
                    <div class="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-1">Role</div>
                    <div class="text-xs text-slate-300">
                        Provides thermal mass and structural support during brazing. Included in physics calculations for accurate cycle generation.
                    </div>
                </div>
            </div>
        `;
        return html;
    }

    const data = materials[partKey];
    if (!data) return '';

    const displayNames = {
        'top': 'Top',
        'filler': 'Filler',
        'bottom': 'Bottom'
    };

    const alloy = data.alloy;
    const materialProps = MATERIAL_PROPERTIES[alloy] || {};
    const geometricProps = data.properties || {};

    // Format geometric properties
    let volumeStr = '0.00';
    if (geometricProps.volume !== undefined && geometricProps.volume !== null) {
        volumeStr = geometricProps.volume.toFixed(2);
    }

    const surfaceArea = geometricProps.surface_area ? geometricProps.surface_area.toFixed(2) : '0.00';
    const vertexCount = geometricProps.vertex_count || '0';
    const faceCount = geometricProps.face_count || '0';

    // Format Dimensions
    let dimensionsStr = 'N/A';
    if (geometricProps.extents && geometricProps.extents.length >= 3) {
        dimensionsStr = geometricProps.extents.map(v => v.toFixed(2)).join(' × ');
    }

    // Format Centroid
    let centroidStr = 'N/A';
    if (geometricProps.centroid && geometricProps.centroid.length >= 3) {
        centroidStr = `(${geometricProps.centroid.map(v => v.toFixed(2)).join(', ')})`;
    }

    let html = `
        <div class="glass-panel p-3 rounded border border-slate-700/50 mb-3">
            <div class="font-bold text-cyan-400 text-sm border-b border-slate-700 pb-2 mb-2">
                ${displayNames[partKey]}
            </div>
            <div class="text-xs text-slate-300 mb-2 font-mono">${alloy}</div>
            
            <div class="mt-2">
                <div class="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-1">Material Properties</div>
                <div class="grid grid-cols-2 gap-x-2 gap-y-1 text-xs font-mono">
                    <div class="text-slate-400">Density:</div><div class="text-slate-200 text-right">${materialProps.density || 'N/A'} <span class="text-slate-600">kg/m³</span></div>
                    <div class="text-slate-400">Therm. Cond:</div><div class="text-slate-200 text-right">${materialProps.thermal_conductivity || 'N/A'} <span class="text-slate-600">W/mK</span></div>
                    <div class="text-slate-400">Spec. Heat:</div><div class="text-slate-200 text-right">${materialProps.specific_heat || 'N/A'} <span class="text-slate-600">J/kgK</span></div>
                    <div class="text-slate-400">Solidus:</div><div class="text-slate-200 text-right">${materialProps.solidus || 'N/A'}<span class="text-slate-600">°C</span></div>
                    <div class="text-slate-400">Liquidus:</div><div class="text-slate-200 text-right">${materialProps.liquidus || 'N/A'}<span class="text-slate-600">°C</span></div>
                </div>
            </div>
            
            <div class="mt-3 pt-2 border-t border-slate-800">
                <div class="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-1">Geometric Analysis</div>
                <div class="grid grid-cols-2 gap-x-2 gap-y-1 text-xs font-mono">
                    <div class="text-slate-400">Volume:</div><div class="text-emerald-400 text-right">${volumeStr} <span class="text-slate-600">mm³</span></div>
                    <div class="text-slate-400">Area:</div><div class="text-blue-400 text-right">${surfaceArea} <span class="text-slate-600">mm²</span></div>
                    <div class="text-slate-400">Vertices:</div><div class="text-slate-200 text-right">${vertexCount}</div>
                    <div class="text-slate-400">Faces:</div><div class="text-slate-200 text-right">${faceCount}</div>
                    <div class="text-slate-400 col-span-2 mt-1">Dimensions:<span class="text-slate-200 float-right">${dimensionsStr}</span></div>
                    <div class="text-slate-400 col-span-2">Centroid:<span class="text-slate-500 float-right text-[10px]">${centroidStr}</span></div>
                </div>
            </div>
        </div>
    `;

    // Add Melting Analysis Section only for the first part shown
    if (partKey === 'top' && meltingAnalysis) {
        let statusColor = 'text-emerald-400';
        let borderColor = 'border-emerald-500/30';

        if (meltingAnalysis.status === 'danger') {
            statusColor = 'text-red-400';
            borderColor = 'border-red-500/30';
        } else if (meltingAnalysis.status === 'warning') {
            statusColor = 'text-amber-400';
            borderColor = 'border-amber-500/30';
        }

        html += `
            <div class="glass-panel p-3 rounded border ${borderColor} mb-3 bg-slate-900/50">
                <div class="font-bold ${statusColor} text-sm border-b border-slate-700 pb-2 mb-2">
                    Melting Analysis
                </div>
                <div class="text-xs font-mono text-slate-300">
                    <div class="mb-1">Filler Liquidus: <span class="text-slate-200">${meltingAnalysis.filler_liquidus}°C</span></div>
                    <div class="mb-1">Base Solidus: <span class="text-slate-200">${meltingAnalysis.base_solidus}°C</span> <span class="text-slate-400 text-xxs">(Material: 640°C)</span></div>
                    <div class="mt-2 ${statusColor} font-bold">${meltingAnalysis.message}</div>
                </div>
            </div>
        `;
    }

    return html;
}

function updateMaterialProperties(materials, meltingAnalysis) {
    // Store data for tab switching
    currentMaterials = materials;
    currentMeltingAnalysis = meltingAnalysis;

    // Log received materials for debugging
    console.log('updateMaterialProperties called with materials:', materials);
    console.log('Fixture data:', materials.fixture);

    // Display the first part (top) by default
    const propertiesContent = document.getElementById('propertiesContent');
    propertiesContent.innerHTML = generatePropertyHTML('top', materials, meltingAnalysis);

    // Set up button event listeners
    const propertyButtons = document.querySelectorAll('.property-btn');
    propertyButtons.forEach(btn => {
        btn.addEventListener('click', (e) => {
            const partKey = e.target.getAttribute('data-part');

            // Update active button styling
            propertyButtons.forEach(b => {
                b.classList.remove('border-cyan-400', 'text-cyan-400', 'bg-slate-800/50');
                b.classList.add('border-slate-600', 'text-slate-300');
            });
            e.target.classList.add('border-cyan-400', 'text-cyan-400', 'bg-slate-800/50');
            e.target.classList.remove('border-slate-600', 'text-slate-300');

            // Update content
            propertiesContent.innerHTML = generatePropertyHTML(partKey, materials, meltingAnalysis);
        });
    });

    // Set the first button as active by default
    if (propertyButtons.length > 0) {
        propertyButtons[0].classList.add('border-cyan-400', 'text-cyan-400', 'bg-slate-800/50');
        propertyButtons[0].classList.remove('border-slate-600', 'text-slate-300');
    }
}

// Show/hide loading indicator
function showLoading(show) {
    const loading = document.getElementById('loading');
    loading.style.display = show ? 'flex' : 'none';
}

// Add a message to the log
function logMessage(message, type = 'info') {
    const logContainer = document.getElementById('log-container');

    // Fallback to console if log container is missing
    if (!logContainer) {
        console.log(`[${type.toUpperCase()}] ${message}`);
        return;
    }

    const messageElement = document.createElement('div');

    const now = new Date();
    const timeString = now.toLocaleTimeString();

    messageElement.innerHTML = `[${timeString}] <span class="${getLogTypeClass(type)}">${message}</span>`;
    logContainer.appendChild(messageElement);
    logContainer.scrollTop = logContainer.scrollHeight;
}

// Get CSS class for log message type
function getLogTypeClass(type) {
    switch (type) {
        case 'error': return 'text-red-500 font-bold';
        case 'warning': return 'text-amber-500';
        case 'success': return 'text-emerald-400';
        default: return 'text-slate-300';
    }
}

// Start the simulation
function startSimulation() {
    if (isSimulationRunning) {
        logMessage('Simulation is already running', 'warning');
        return;
    }

    const targetTemp = parseFloat(document.getElementById('targetTemp').value);
    const heatingRate = parseFloat(document.getElementById('heatingRate').value);
    const dwellTime = parseFloat(document.getElementById('dwellTime').value);

    if (isNaN(targetTemp) || isNaN(heatingRate) || isNaN(dwellTime)) {
        logMessage('Please enter valid simulation parameters', 'error');
        return;
    }

    logMessage(`Starting simulation: Target=${targetTemp}°C, Rate=${heatingRate}°C/s, Dwell=${dwellTime}s`);
    connectWebSocket();
}

// Initialize the application
function init() {
    // Initialize 3D scene
    initScene();

    // Set up event listeners
    const uploadForm = document.getElementById('uploadForm');
    const fillerInput = document.getElementById('filler');
    const fillerTypeSelect = document.getElementById('fillerType');
    const selectedFillerSpan = document.getElementById('selectedFiller');
    const fillerFileName = document.getElementById('fillerFileName');

    // Update selected filler material display
    function updateSelectedFiller() {
        const selectedValue = fillerTypeSelect.value;
        selectedFillerSpan.textContent = selectedValue;

        // Update the file input's name based on selection
        fillerInput.name = `filler_${selectedValue.toLowerCase()}`;

        // Update the log
        logMessage(`Filler material set to ${selectedValue}`);
    }

    // Handle file selection
    fillerInput.addEventListener('change', (e) => {
        const file = e.target.files[0];
        if (file) {
            if (fillerFileName) fillerFileName.textContent = `Selected: ${file.name}`;
            logMessage(`Selected filler file: ${file.name}`);
        } else {
            if (fillerFileName) fillerFileName.textContent = 'No file selected';
        }
    });

    // Handle form submission
    uploadForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        await uploadFiles();
    });

    // Handle filler type change
    fillerTypeSelect.addEventListener('change', updateSelectedFiller);

    // Initialize the selected filler display
    updateSelectedFiller();

    // Set up simulation start button
    document.getElementById('startSimulation').addEventListener('click', startSimulation);

    // Set up recalculate cycle button
    const updateCycleBtn = document.getElementById('updateCycleBtn');
    if (updateCycleBtn) {
        updateCycleBtn.addEventListener('click', async () => {
            await getBrazingCycle();
        });
    }

    // Disable simulation controls until files are uploaded
    document.getElementById('simulationParams').style.opacity = 0.5;
    document.getElementById('simulationParams').style.pointerEvents = 'none';

    logMessage('Application initialized. Please upload 3D models to begin.');
}

// Material properties (matching the backend)
const MATERIAL_PROPERTIES = {
    '6061-T6': {
        density: 2700,
        thermal_conductivity: 167,
        specific_heat: 900,
        solidus: 640,  // Updated to match materials.py
        liquidus: 652,
        color: [0.1, 0.1, 0.8, 1.0]
    },
    '3003-O': {
        density: 2730,
        thermal_conductivity: 160,
        specific_heat: 880,
        solidus: 630,
        liquidus: 655,
        color: [0.8, 0.8, 0.1, 1.0]
    },
    'AL718': {
        density: 2700,
        thermal_conductivity: 160,
        specific_heat: 900,
        solidus: 577,
        liquidus: 582,
        color: [0.9, 0.7, 0.1, 1.0]
    },
    'SS316L': {
        density: 8000,
        thermal_conductivity: 16,
        specific_heat: 500,
        solidus: 1370,
        liquidus: 1400,
        color: [0.7, 0.7, 0.7, 1.0]
    }
};

// Start the application when the page loads
window.addEventListener('DOMContentLoaded', init);

async function analyzeCycle() {
    const tableBody = document.getElementById('brazingCycleBody');
    const resultDiv = document.getElementById('aiAnalysisResult');
    const loadingDiv = document.getElementById('aiLoading');
    const contentDiv = document.getElementById('aiContent');

    // Clear previous results
    resultDiv.classList.remove('hidden');
    loadingDiv.classList.remove('hidden');
    contentDiv.classList.add('hidden');
    contentDiv.textContent = '';

    try {
        // Scrape table data
        const rows = Array.from(tableBody.querySelectorAll('tr'));
        if (rows.length === 0) {
            throw new Error("No cycle data found. Please generate a cycle first.");
        }

        const stages = rows.map(row => {
            const cells = row.querySelectorAll('td');
            // Assuming column order: Stage, Temp, Ramp, Hold, Job1, Job2, Purpose
            if (cells.length < 7) return null;

            return {
                "Stage": cells[0].textContent.trim(),
                "Temperature": parseFloat(cells[1].textContent.replace('°C', '')),
                "RampRate": parseFloat(cells[2].textContent.replace('°C/min', '')),
                "HoldTime": parseFloat(cells[3].textContent.replace('min', '')),
                "Job1Temp": parseFloat(cells[4].textContent.replace('°C', '')),
                "Job2Temp": parseFloat(cells[5].textContent.replace('°C', '')),
                "Purpose": cells[6].textContent.trim()
            };
        }).filter(s => s !== null);

        // Send to backend
        const response = await fetch('/analyze-cycle', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ stages: stages })
        });

        if (!response.ok) {
            const errorText = await response.text();
            throw new Error(`Server error: ${errorText}`);
        }

        const data = await response.json();

        // Display result
        loadingDiv.classList.add('hidden');
        contentDiv.classList.remove('hidden');

        // Simple type-writer effect
        const text = data.analysis;
        let i = 0;
        contentDiv.textContent = '';
        const speed = 10; // ms per char

        function typeWriter() {
            if (i < text.length) {
                contentDiv.textContent += text.charAt(i);
                i++;
                setTimeout(typeWriter, speed);
            }
        }
        typeWriter();

    } catch (error) {
        console.error("Analysis failed:", error);
        loadingDiv.classList.add('hidden');
        contentDiv.classList.remove('hidden');
        contentDiv.textContent = `Analysis failed: ${error.message}`;
        contentDiv.classList.add('text-red-400');
    }
}
