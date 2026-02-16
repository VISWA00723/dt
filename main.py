from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, File, UploadFile, HTTPException, Depends, Form
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Dict, Optional, Union, Any
from pydantic import BaseModel
import uvicorn
import os
import json
import time
import uuid
import numpy as np
import trimesh
import open3d as o3d
import concurrent.futures
import traceback
import asyncio
from scipy.spatial import ConvexHull
from pathlib import Path
from datetime import datetime
import asyncio
import pyiges
import gmsh

from fem_simulation import FEMHeatSimulation
from enhanced_visualization import EnhancedVisualization
from simulation_parameters import generate_brazing_cycle
from llm_service import analyze_temperature_difference


app = FastAPI(title="Vacuum Brazing Simulator")

# Mount static files for frontend
app.mount("/static", StaticFiles(directory="static"), name="static")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from materials import MATERIAL_DB

class SimulationState:
    def __init__(self):
        self.meshes = {}
        self.combined_mesh = None
        self.simulation_running = False
        self.current_temperature = 25.0  # °C
        self.target_temperature = 540.0  # °C
        self.time_step = 0.1  # s
        self.simulation_time = 0.0  # s
        self.temperature_field = None
        self.mesh_data = None
        # Enhanced components
        self.fem_simulator = None
        self.visualizer = EnhancedVisualization()
        # Carbon sheet configuration
        self.carbon_sheet_thickness_mm = 0.8  # Default carbon sheet thickness in mm


simulation = SimulationState()

# Create uploads directory if it doesn't exist
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

    
def calculate_geometric_properties(mesh) -> Dict:
    """Calculate comprehensive geometric properties of a mesh or point cloud."""
    from trimesh.base import Trimesh
    from trimesh.points import PointCloud
    from trimesh.repair import fix_normals, fix_inversion
    
    properties = {
        "type": "point_cloud" if isinstance(mesh, PointCloud) else "mesh",
        "is_empty": bool(mesh.is_empty) if hasattr(mesh, 'is_empty') else None,
        "volume": 0.0,  # Default
        "surface_area": 0.0
    }
    
    # Common properties for both mesh and point cloud
    if hasattr(mesh, 'vertices'):
        vertices = mesh.vertices
        # Calculate extents first as it's needed for volume fallback
        extents = (vertices.max(axis=0) - vertices.min(axis=0)).tolist()
        properties.update({
            "vertex_count": len(vertices),
            "bounds": vertices.min(axis=0).tolist() + vertices.max(axis=0).tolist(),
            "extents": extents,
            "centroid": vertices.mean(axis=0).tolist(),
        })
    
    # Mesh-specific properties
    if isinstance(mesh, Trimesh):
        working_mesh = mesh.copy()
        
        # Try to fix common mesh issues
        try:
            if not working_mesh.is_watertight:
                # Try to fix normals and inversion
                fix_normals(working_mesh, multibody=True)
                fix_inversion(working_mesh, multibody=True)
                
                # Try to make the mesh watertight
                working_mesh = working_mesh.convex_hull if not working_mesh.is_watertight else working_mesh
        except Exception as e:
            print(f"Warning: Could not repair mesh: {e}")
        
        # Calculate properties
        try:
            # 1. Try standard volume (requires watertight)
            volume = 0.0
            if working_mesh.is_watertight:
                volume = abs(working_mesh.volume)
                print(f"Watertight mesh volume: {volume}")
            else:
                # Fallback: Approximate Volume from Bounding Box
                extents = properties.get("extents", [0, 0, 0])
                bbox_vol = extents[0] * extents[1] * extents[2]
                print(f"WARNING: Mesh not watertight. Using Bounding Box Volume approximation: {bbox_vol} mm3")
                volume = bbox_vol * 0.5 # Assume 50% fill factor for open shapes

            surface_area = working_mesh.area
            face_count = len(working_mesh.faces) if hasattr(working_mesh, 'faces') else 0
            
            properties.update({
                "volume": float(volume),
                "surface_area": float(surface_area) if surface_area is not None else 0.0,
                "face_count": face_count,
                "is_watertight": bool(working_mesh.is_watertight) if hasattr(working_mesh, 'is_watertight') else None,
                "euler_number": int(working_mesh.euler_number) if hasattr(working_mesh, 'euler_number') else None,
                "is_winding_consistent": bool(working_mesh.is_winding_consistent) if hasattr(working_mesh, 'is_winding_consistent') else None,
            })
            
            
            # Calculate mass properties if the mesh is watertight
            if working_mesh.is_watertight and volume > 0:
                try:
                    mass_props = working_mesh.mass_properties
                    properties.update({
                        "mass": float(mass_props["mass"]) if mass_props["mass"] is not None else 0.0,
                        "density": float(mass_props["density"]) if mass_props["density"] is not None else 0.0,
                        "center_of_mass": mass_props["center_mass"].tolist() if hasattr(mass_props["center_mass"], 'tolist') else mass_props["center_mass"],
                    })
                except Exception as e:
                    print(f"Warning: Could not calculate mass properties: {e}")
                    
        except Exception as e:
            print(f"Error calculating mesh properties: {e}")
            # Ensure volume has at least bbox fallback on error
            extents = properties.get("extents", [0, 0, 0])
            bbox_vol = extents[0] * extents[1] * extents[2]
            properties.update({
                "volume": float(bbox_vol * 0.5), # Approx
                "face_count": 0,
                "is_watertight": False,
            })
    
    # For point clouds, we can calculate some additional metrics
    elif isinstance(mesh, PointCloud):
        properties.update({
            "face_count": 0,
            "is_watertight": False,
        })
        
        # Try to calculate convex hull for point cloud
        try:
            from scipy.spatial import ConvexHull
            if hasattr(mesh, 'vertices') and len(mesh.vertices) >= 4:  # Need at least 4 points for 3D convex hull
                hull = ConvexHull(mesh.vertices)
                properties.update({
                    "convex_hull_volume": float(hull.volume),
                    "convex_hull_area": float(hull.area),
                    "convex_hull_vertices": len(hull.vertices)
                })
                
                # Use convex hull volume as an estimate
                properties["volume"] = float(hull.volume)
                properties["is_volume_estimated"] = True
            else:
                 # Fallback for small point clouds
                 extents = properties.get("extents", [0, 0, 0])
                 properties["volume"] = extents[0] * extents[1] * extents[2] * 0.5
        except Exception as e:
            print(f"Warning: Could not calculate convex hull: {e}")
            # Fallback
            extents = properties.get("extents", [0, 0, 0])
            properties["volume"] = extents[0] * extents[1] * extents[2] * 0.5
    
    # Add dimensional properties
    if "extents" in properties:
        extents = properties["extents"]
        properties.update({
            "length": float(extents[0]) if len(extents) > 0 else 0.0,
            "width": float(extents[1]) if len(extents) > 1 else 0.0,
            "height": float(extents[2]) if len(extents) > 2 else 0.0,
            "dimensions": [float(x) for x in extents] if extents else [0.0, 0.0, 0.0]
        })

        # Heuristic: auto-correct volume for thin, plate-like geometries
        # Assumes extents are in millimetres (as in your IGES), converts to metres for volume.
        try:
            if len(extents) >= 3:
                # Absolute values, sort so dims_mm[0] is the smallest (thickness)
                dims_mm = sorted([abs(float(d)) for d in extents])
                thickness_mm, big1_mm, big2_mm = dims_mm[0], dims_mm[1], dims_mm[2]
                
                # debug_msg = f"DEBUG: Plate check - dims_mm={dims_mm}, thickness={thickness_mm}, big1={big1_mm}, big2={big2_mm}"
                # print(debug_msg)
                
                test1 = thickness_mm < 0.25 * big1_mm
                test2 = thickness_mm < 0.25 * big2_mm
                
                # Thin-plate check: one dimension much smaller than the other two
                if thickness_mm > 0 and test1 and test2:
                    # Use mm directly
                    corrected_volume = big1_mm * big2_mm * thickness_mm  # mm^3
                    
                    # debug_msg3 = f"DEBUG: Plate detected! Computing volume: {big1_mm} * {big2_mm} * {thickness_mm} = {corrected_volume} mm³"
                    # print(debug_msg3)

                    # Preserve any previously-computed mesh volume for debugging
                    if "volume" in properties and properties["volume"] not in (None, 0.0):
                        properties["raw_volume"] = float(properties["volume"])

                    properties["volume"] = float(corrected_volume)
                    properties["volume_corrected"] = True
                    # debug_msg4 = f"DEBUG: Volume corrected to {corrected_volume} mm³"
                    # print(debug_msg4)
        except Exception as e:
            print(f"Warning: plate-like volume correction failed: {e}")
            import traceback
            traceback.print_exc()
            
    # Final safety check for volume
    if properties.get("volume", 0) <= 0:
        print("Warning: Volume is zero or negative, utilizing bounds fallback.")
        extents = properties.get("extents", [1, 1, 1])
        properties["volume"] = max(1.0, extents[0] * extents[1] * extents[2] * 0.5)

    return properties

def process_iges_file(file_path: Path, part_type: str) -> Dict:
    """
    Process an IGES file using multiple methods for robustness.
    Ensures we get a proper mesh with faces, not a point cloud.
    
    Args:
        file_path: Path to the IGES file
        part_type: Type of part ('top', 'bottom', 'filler')
        
    Returns:
        Dict containing geometric properties
    """
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    
    mesh = None
    
    # Method 1: Try direct trimesh loading (simplest, fastest)
    print(f"\n[Method 1] Trying direct trimesh load for {part_type}...")
    try:
        mesh = trimesh.load(str(file_path), process=False)
        if mesh is not None and len(mesh.vertices) > 0:
            print(f"  Loaded mesh type: {type(mesh).__name__}")
            
            if isinstance(mesh, trimesh.Scene):
                # If it's a scene with multiple meshes, merge them
                print(f"  Scene with {len(mesh.geometry)} geometries, merging...")
                mesh = trimesh.util.concatenate(mesh.geometry.values())
                print(f"  Merged to type: {type(mesh).__name__}")
            
            # Check if we have faces
            if isinstance(mesh, trimesh.PointCloud):
                print(f"⚠ Method 1: Loaded as PointCloud ({len(mesh.vertices)} points) - will try other methods")
                mesh = None
            elif hasattr(mesh, 'faces') and len(mesh.faces) > 0:
                print(f"✓ Method 1 SUCCESS: Loaded {len(mesh.vertices)} vertices, {len(mesh.faces)} faces")
            else:
                print(f"⚠ Method 1: Loaded {len(mesh.vertices)} vertices but NO FACES - will try other methods")
                mesh = None
    except Exception as e:
        print(f"✗ Method 1 failed: {str(e)}")
        import traceback
        traceback.print_exc()
        mesh = None
    
    # Method 2: Use GMSH with surface mesh extraction (if Method 1 failed or had no faces)
    if mesh is None or (hasattr(mesh, 'faces') and len(mesh.faces) == 0):
        print(f"\n[Method 2] Trying GMSH surface mesh extraction for {part_type}...")
        gmsh.initialize()
        try:
            gmsh.option.setNumber("Mesh.CharacteristicLengthMin", 0.5)
            gmsh.option.setNumber("Mesh.CharacteristicLengthMax", 2.0)
            gmsh.option.setNumber("Mesh.Algorithm", 6)
            gmsh.option.setNumber("Mesh.Algorithm3D", 10)
            
            gmsh.open(str(file_path))
            
            # First, try to get the geometry information
            gmsh.model.geo.synchronize()
            
            # Generate 2D surface mesh
            gmsh.model.mesh.generate(2)  # Generate 2D surface mesh only
            
            node_tags, node_coords, _ = gmsh.model.mesh.getNodes()
            vertices = node_coords.reshape(-1, 3)
            print(f"  GMSH extracted {len(vertices)} vertices")
            
            # Get 2D surface elements (triangles)
            element_types_2d, element_tags_2d, node_tags_2d = gmsh.model.mesh.getElements(2)
            print(f"  GMSH found {len(element_types_2d)} element types")
            
            faces = []
            if len(element_types_2d) > 0:
                for i, elem_type in enumerate(element_types_2d):
                    print(f"    Element type {i}: {elem_type}")
                    if elem_type == 2:  # Triangle
                        tri_faces = node_tags_2d[i].reshape(-1, 3)
                        faces.append(tri_faces)
                        print(f"    Found {len(tri_faces)} triangles")
            
            if len(faces) > 0:
                faces = np.vstack(faces) - 1  # Convert to 0-based indexing
                mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=True)
                print(f"✓ Method 2 SUCCESS: Created mesh with {len(vertices)} vertices and {len(faces)} faces")
            else:
                print("✗ Method 2: No triangle faces extracted")
                mesh = None
                
        except Exception as e:
            print(f"✗ Method 2 failed: {str(e)}")
            import traceback
            traceback.print_exc()
            mesh = None
        finally:
            gmsh.finalize()
    
    # Method 3: If still no mesh with faces, try to reconstruct from point cloud
    if mesh is None or (hasattr(mesh, 'faces') and len(mesh.faces) == 0):
        print(f"\n[Method 3] Attempting mesh reconstruction from point cloud for {part_type}...")
        try:
            # If we have a point cloud from Method 1, use it; otherwise try to load again
            if mesh is None:
                mesh = trimesh.load(str(file_path), process=False)
            
            if mesh is not None and hasattr(mesh, 'vertices') and len(mesh.vertices) > 0:
                points = np.array(mesh.vertices, dtype=np.float64)
                print(f"  Using {len(points)} points for reconstruction")
                
                # Try Open3D ball pivoting algorithm
                try:
                    import open3d as o3d
                    print("  Attempting ball pivoting reconstruction...")
                    
                    pcd = o3d.geometry.PointCloud()
                    pcd.points = o3d.utility.Vector3dVector(points)
                    
                    # Estimate normals (required for ball pivoting)
                    pcd.estimate_normals()
                    pcd.orient_normals_consistent_tangent_plane(100)
                    
                    # Ball pivoting reconstruction
                    radii = [0.1, 0.2, 0.4, 0.8]
                    mesh_o3d = o3d.geometry.TriangleMesh.create_from_point_cloud_ball_pivoting(
                        pcd, o3d.utility.DoubleVector(radii))
                    
                    vertices = np.asarray(mesh_o3d.vertices)
                    faces = np.asarray(mesh_o3d.triangles)
                    
                    if len(vertices) > 0 and len(faces) > 0:
                        mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=True)
                        print(f"✓ Method 3 SUCCESS (ball pivoting): Created mesh with {len(vertices)} vertices and {len(faces)} faces")
                    else:
                        raise ValueError("Ball pivoting produced empty mesh")
                        
                except ImportError:
                    print("  Open3D not available, trying Delaunay triangulation...")
                    # Fallback to Delaunay triangulation
                    try:
                        from scipy.spatial import Delaunay, ConvexHull
                        hull = ConvexHull(points)
                        mesh = trimesh.Trimesh(vertices=points, faces=hull.simplices, process=True)
                        print(f"✓ Method 3 SUCCESS (convex hull): Created mesh with {len(points)} vertices and {len(hull.simplices)} faces")
                    except Exception as e:
                        print(f"  Delaunay triangulation failed: {str(e)}")
                        raise ValueError(f"Could not reconstruct mesh: {str(e)}")
                        
                except Exception as e:
                    print(f"  Ball pivoting failed: {str(e)}")
                    # Try Delaunay as fallback
                    try:
                        from scipy.spatial import ConvexHull
                        hull = ConvexHull(points)
                        mesh = trimesh.Trimesh(vertices=points, faces=hull.simplices, process=True)
                        print(f"✓ Method 3 SUCCESS (convex hull fallback): Created mesh with {len(points)} vertices and {len(hull.simplices)} faces")
                    except Exception as e2:
                        print(f"  Convex hull also failed: {str(e2)}")
                        raise ValueError(f"Could not reconstruct mesh: {str(e2)}")
            else:
                raise ValueError("No vertices found to reconstruct mesh")
                
        except Exception as e:
            error_msg = f"Could not extract or reconstruct mesh with faces from IGES file {file_path}. Error: {str(e)}"
            print(f"✗ Method 3: {error_msg}")
            raise ValueError(error_msg)
    
    # Calculate geometric properties
    properties = calculate_geometric_properties(mesh)
    
    # Add part type and basic info
    properties.update({
        "part_type": part_type,
        "file_name": file_path.name,
        "file_size": file_path.stat().st_size,
        "vertex_count": len(mesh.vertices),
        "face_count": len(mesh.faces) if hasattr(mesh, 'faces') else 0,
        "mesh": mesh  # Include the actual mesh object for later use
    })
    
    return properties

def validate_iges_file(file: UploadFile) -> None:
    """Validate that the uploaded file is an IGES file."""
    if not file.filename.lower().endswith('.igs'):
        raise HTTPException(status_code=400, detail=f"File {file.filename} is not an IGES file (.igs)")
    
    # Check file content to ensure it's a valid IGES file
    content = file.file.read(1024).decode('utf-8', errors='ignore')
    if 'IGES' not in content[:128]:  # IGES files typically start with this
        raise HTTPException(status_code=400, detail=f"File {file.filename} is not a valid IGES file")
    file.file.seek(0)  # Reset file pointer

def analyze_melting_compatibility(filler_type: str, base_type: str = "6061-T6") -> Dict[str, Any]:
    """
    Analyze if the filler material will melt correctly without melting the base metal.
    """
    filler_props = MATERIAL_DB.get(filler_type)
    base_props = MATERIAL_DB.get(base_type)
    
    if not filler_props or not base_props:
        return {
            "status": "unknown",
            "message": "Unknown material properties",
            "details": {}
        }
        
    filler_liquidus = filler_props['liquidus']
    base_solidus = base_props['solidus']
    
    # Calculate process window
    process_window = base_solidus - filler_liquidus
    
    analysis = {
        "filler_liquidus": filler_liquidus,
        "base_solidus": base_solidus,
        "process_window": process_window,
        "status": "safe",
        "message": "Process window is adequate."
    }
    
    if process_window < 0:
        analysis["status"] = "danger"
        analysis["message"] = f"DANGER: Filler liquidus ({filler_liquidus}°C) exceeds Base solidus ({base_solidus}°C). Base metal will melt!"
    elif process_window < 10:
        analysis["status"] = "warning"
        analysis["message"] = f"WARNING: Very narrow process window ({process_window}°C). Precise temperature control required."
    else:
        analysis["message"] = f"SAFE: Good process window of {process_window}°C."
        
    return analysis

@app.post("/upload")
async def upload_files(
    top_plate: UploadFile = File(...),
    filler: UploadFile = File(...),
    bottom_plate: UploadFile = File(...),
    fixture: UploadFile = File(None),  # Optional fixture file
    filler_type: str = Form("AL718"),  # Default to AL718 if not provided
    carbon_sheet_thickness_mm: float = Form(0.8)  # Carbon sheet thickness in mm
):
    """Handle file uploads for the four components (Top, Filler, Bottom, Fixture)."""
    import traceback  # Add traceback import here
    
    try:
        print(f"Received upload request with filler type: {filler_type}, carbon sheet: {carbon_sheet_thickness_mm}mm")
        
        # Store carbon sheet thickness in simulation state
        simulation.carbon_sheet_thickness_mm = carbon_sheet_thickness_mm

        
        # Validate filler type
        valid_filler_types = ["AL718", "3003-O"]
        if filler_type not in valid_filler_types:
            error_msg = f"Invalid filler type: {filler_type}. Must be one of {', '.join(valid_filler_types)}"
            print(error_msg)
            raise HTTPException(status_code=400, detail=error_msg)
        
        # Validate file types
        print("Validating file types...")
        files = [
            ("top_plate", top_plate),
            ("filler", filler),
            ("bottom_plate", bottom_plate)
        ]
        
        # Add fixture to files list if provided
        if fixture and fixture.filename:
            files.append(("fixture", fixture))
        
        for name, file in files:
            if not file.filename:
                error_msg = f"No file provided for {name.replace('_', ' ')}"
                print(error_msg)
                raise HTTPException(status_code=400, detail=error_msg)
            
            try:
                validate_iges_file(file)
                print(f"Validated {name}: {file.filename}")
            except HTTPException as he:
                print(f"Validation failed for {name}: {str(he.detail)}")
                raise
        
        # Create upload directory with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        upload_dir = UPLOAD_DIR / timestamp
        upload_dir.mkdir(parents=True, exist_ok=True)
        print(f"Created upload directory: {upload_dir}")
        
        # Save uploaded files with original names
        file_paths = {
            "top": upload_dir / "top.igs",
            "filler": upload_dir / "filler.igs",
            "bottom": upload_dir / "bottom.igs"
        }
        
        # Add fixture path if provided
        if fixture and fixture.filename:
            file_paths["fixture"] = upload_dir / "fixture.igs"
        
        # Save files
        print("Saving uploaded files...")
        for (name, file), path in zip(files, [file_paths[k] for k in ["top", "filler", "bottom"] + (["fixture"] if fixture and fixture.filename else [])]):
            try:
                content = await file.read()
                with open(path, "wb") as f:
                    f.write(content)
                print(f"Saved {name} to {path}")
            except Exception as e:
                error_msg = f"Error saving {name} file: {str(e)}"
                print(error_msg)
                raise HTTPException(status_code=500, detail=error_msg)
        
        # Process the files and create meshes
        print("Processing files...")
        simulation.meshes = {}
        results = {}
        
        for part, path in file_paths.items():
            try:
                print(f"Processing {part} file: {path}")
                # Process the file
                result = process_iges_file(path, part)
                
                # Store the result
                results[part] = result
                
                # Determine alloy type
                if part == 'filler':
                    alloy_name = filler_type
                elif part == 'fixture':
                    alloy_name = "SS316L"
                else:
                    alloy_name = "6061-T6"
                
                # Add to simulation
                simulation.meshes[part] = {
                    'file_path': str(path),
                    'alloy': alloy_name,
                    'material': MATERIAL_DB[alloy_name],
                    'properties': result,
                    'mesh': result.get('mesh')  # Extract the mesh object from properties
                }
                print(f"Processed {part} successfully")
                
            except Exception as e:
                error_msg = f"Error processing {part} file: {str(e)}"
                print(error_msg)
                print(traceback.format_exc())
                raise HTTPException(status_code=500, detail=error_msg)
        
        # Combine meshes for simulation
        try:
            print("Combining meshes...")
            combined_mesh = combine_meshes(simulation.meshes)
            print("Meshes combined successfully")
            
            if combined_mesh and "vertices" in combined_mesh:
                try:
                    # Create a trimesh object if we have faces, otherwise create a point cloud
                    if "faces" in combined_mesh and combined_mesh["faces"] is not None and len(combined_mesh["faces"]) > 0:
                        simulation.combined_mesh = trimesh.Trimesh(
                            vertices=combined_mesh["vertices"],
                            faces=combined_mesh["faces"]
                        )
                        print(f"Created trimesh with {len(combined_mesh['vertices'])} vertices and {len(combined_mesh['faces'])} faces")
                    else:
                        simulation.combined_mesh = trimesh.PointCloud(vertices=combined_mesh["vertices"])
                        print(f"Created point cloud with {len(combined_mesh['vertices'])} points")
                    
                    # Check mesh size and simplify if too large for FEM
                    if "vertices" in combined_mesh and len(combined_mesh["vertices"]) > 50000:
                        print(f"Mesh too large for FEM ({len(combined_mesh['vertices'])} vertices), simplifying...")
                        
                        # Create point cloud and simplify
                        temp_cloud = trimesh.PointCloud(vertices=combined_mesh["vertices"])
                        
                        # Try to use Open3D for voxel downsampling, fallback to random sampling
                        try:
                            import open3d as o3d
                            print("Using Open3D for mesh simplification...")
                            
                            # Convert to Open3D point cloud
                            pcd = o3d.geometry.PointCloud()
                            pcd.points = o3d.utility.Vector3dVector(temp_cloud.vertices)
                            
                            # Voxel downsampling (reduce to ~10k-20k points)
                            voxel_size = 0.005  # 5mm voxels
                            downsampled_pcd = pcd.voxel_down_sample(voxel_size=voxel_size)
                            
                            # Update combined_mesh with simplified vertices
                            simplified_vertices = np.asarray(downsampled_pcd.points)
                            combined_mesh["vertices"] = simplified_vertices
                            
                            # Remove faces since we're now dealing with a point cloud
                            if "faces" in combined_mesh:
                                del combined_mesh["faces"]
                            
                            print(f"Simplified mesh to {len(simplified_vertices)} vertices using voxel downsampling")
                            
                            # Update the trimesh object with simplified vertices
                            simulation.combined_mesh = trimesh.PointCloud(vertices=simplified_vertices)
                            
                        except ImportError:
                            print("Open3D not available, using random sampling instead")
                            # Fallback: random sampling
                            n_samples = min(5000, len(combined_mesh["vertices"]))
                            indices = np.random.choice(len(combined_mesh["vertices"]), n_samples, replace=False)
                            combined_mesh["vertices"] = combined_mesh["vertices"][indices]
                            
                            # Update the trimesh object with simplified vertices
                            simulation.combined_mesh = trimesh.PointCloud(vertices=combined_mesh["vertices"])
                            
                            print(f"Simplified mesh to {n_samples} vertices using random sampling")
                        except Exception as e:
                            print(f"Mesh simplification failed: {e}, using random sampling instead")
                            # Fallback: random sampling
                            n_samples = min(5000, len(combined_mesh["vertices"]))
                            indices = np.random.choice(len(combined_mesh["vertices"]), n_samples, replace=False)
                            combined_mesh["vertices"] = combined_mesh["vertices"][indices]
                            
                            # Update the trimesh object with simplified vertices
                            simulation.combined_mesh = trimesh.PointCloud(vertices=combined_mesh["vertices"])
                            
                            print(f"Simplified mesh to {n_samples} vertices using random sampling")
                    
                    # Initialize FEM simulator with combined mesh
                    print("Initializing FEM simulator...")
                    material_props = MATERIAL_DB["6061-T6"]  # Default material
                    simulation.fem_simulator = FEMHeatSimulation(combined_mesh, material_props)
                    print("FEM simulator initialized successfully")
                    
                except Exception as e:
                    print(f"Warning: Could not create trimesh object: {str(e)}")
                    traceback.print_exc()
                    # Fall back to just returning the vertices as a point cloud
                    simulation.combined_mesh = trimesh.PointCloud(vertices=combined_mesh["vertices"])
                    
                    # Still try to initialize FEM simulator
                    try:
                        material_props = MATERIAL_DB["6061-T6"]
                        simulation.fem_simulator = FEMHeatSimulation(combined_mesh, material_props)
                        print("FEM simulator initialized with point cloud")
                    except Exception as fem_error:
                        print(f"Warning: Could not initialize FEM simulator: {str(fem_error)}")
                        
        except Exception as e:
            error_msg = f"Error combining meshes: {str(e)}"
            print(error_msg)
            print(traceback.format_exc())
            raise HTTPException(status_code=500, detail=error_msg)
        
        # Calculate target brazing temperature
        print("Calculating target temperature...")
        base_alloys = ["6061-T6"]  # Default base alloy
        target_temp = compute_target_braze_temp(filler_type, base_alloys)
        simulation.target_temperature = target_temp
        
        # Perform melting analysis
        melting_analysis = analyze_melting_compatibility(filler_type, "6061-T6")
        
        # Prepare response data with JSON-serializable results
        json_safe_results = {}
        for part, data in results.items():
            # Create a copy without the mesh object
            json_safe_results[part] = {k: v for k, v in data.items() if k != 'mesh'}
        
        response_data = {
            "status": "success",
            "message": "Files uploaded and processed successfully",
            "results": json_safe_results,
            "target_temperature": target_temp,
            "melting_analysis": melting_analysis,
            "meshes": {}
        }
        
        # Add mesh data to response
        for part, data in results.items():
            # Skip the 'combined' key if it exists
            if part == "combined":
                continue
            
            if part in simulation.meshes:
                # Use the JSON-safe version without mesh object
                response_data["meshes"][part] = {
                    "alloy": simulation.meshes[part].get("alloy", "Unknown"),
                    "material": simulation.meshes[part]["material"],
                    "properties": json_safe_results[part]
                }
        
        # Save results to file
        results_file = upload_dir / "results.json"
        try:
            with open(results_file, 'w') as f:
                # Use a custom JSON encoder to handle numpy types and None values
                class NumpyEncoder(json.JSONEncoder):
                    def default(self, obj):
                        if isinstance(obj, np.integer):
                            return int(obj)
                        if isinstance(obj, np.floating):
                            return float(obj)
                        if isinstance(obj, np.ndarray):
                            return obj.tolist()
                        if isinstance(obj, np.bool_):
                            return bool(obj)
                        return super().default(obj)
                
                json.dump(response_data, f, indent=2, cls=NumpyEncoder)
                print(f"Results saved to {results_file}")
                
        except Exception as e:
            error_msg = f"Error saving results to file: {str(e)}"
            print(error_msg)
            raise HTTPException(status_code=500, detail=error_msg)
        
        return response_data
    
    except HTTPException as he:
        print(f"HTTP Exception: {he.detail}")
        raise he
    except Exception as e:
        error_details = f"Error processing files: {str(e)}\n\nTraceback:\n{traceback.format_exc()}"
        print(error_details)  # Log the full error to console
        raise HTTPException(status_code=500, detail=f"Error processing files: {str(e)}")

@app.get("/simulation-parameters")
async def get_simulation_parameters(
    initial_temp: Optional[float] = None,
    initial_ramp: Optional[float] = None
):
    """
    Generate and return physics-based brazing cycle parameters.
    
    Optional Query Params:
    - initial_temp: Override for Stage 1 start temperature (°C)
    - initial_ramp: Override for Stage 1 ramp rate (°C/min)
    
    Physics-based computation is the default and only mode.
    All stages are computed from:
    - Thermal mass (geometry + material properties)
    - Characteristic thickness
    - Biot number (conduction vs convection dominance)
    - Thermal diffusivity
    - Filler liquidus and base metal solidus
    
    All temperatures and ramp rates are clamped to machine limits:
    - Temperature: 400-650°C
    - Ramp Rate: 1-10°C/min
    """
    try:
        if not simulation.meshes:
            raise HTTPException(status_code=400, detail="No simulation data available. Please upload files first.")
            
        print("Generating physics-based brazing cycle...")
        print(f"Generating physics-based brazing cycle (overrides: temp={initial_temp}, ramp={initial_ramp})...")
        result = generate_brazing_cycle(simulation, initial_temp=initial_temp, initial_ramp=initial_ramp)
        
        if isinstance(result, dict):
            parameters = result["cycle"]
            verdict = result.get("final_verdict")
            details = result.get("verdict_details")
        else:
            parameters = result
            verdict = "UNKNOWN"
            details = {}
        
        if not parameters:
            raise HTTPException(status_code=500, detail="Failed to generate physics-based cycle")
            
        return {
            "status": "success",
            "message": "Physics-based brazing cycle generated successfully",
            "parameters": parameters,
            "verdict": verdict,
            "verdict_details": details
        }
    except Exception as e:
        print(f"Error generating parameters: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        print(f"Error generating parameters: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
class StageOverride(BaseModel):
    Stage: str
    Temperature: float
    RampRate: float
    HoldTime: float
    Purpose: str

class CycleOverrideRequest(BaseModel):
    stages: List[StageOverride]

@app.post("/simulation-parameters")
async def calculate_custom_cycle(request: CycleOverrideRequest):
    """
    Calculate Job temperatures for a custom user-defined cycle.
    """
    try:
        if not simulation.meshes:
            raise HTTPException(status_code=400, detail="No simulation data available. Please upload files first.")
            
        print(f"Calculating physics for custom cycle with {len(request.stages)} stages...")
        
        # Convert Pydantic models to list of dicts
        overrides = [stage.dict() for stage in request.stages]
        
        # Pass overrides to generate_brazing_cycle
        result = generate_brazing_cycle(simulation, overrides=overrides)
        
        if isinstance(result, dict):
            parameters = result["cycle"]
            verdict = result.get("final_verdict")
            details = result.get("verdict_details")
        else:
            parameters = result
            verdict = "UNKNOWN"
            details = {}
        
        if not parameters:
            raise HTTPException(status_code=500, detail="Failed to calculate custom cycle")
            
        return {
            "status": "success",
            "message": "Custom cycle calculated successfully",
            "parameters": parameters,
            "verdict": verdict,
            "verdict_details": details
        }
    except Exception as e:
        print(f"Error calculating custom cycle: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

class CycleAnalysisRequest(BaseModel):
    stages: List[Dict[str, Any]]

@app.post("/analyze-cycle")
async def analyze_cycle(request: CycleAnalysisRequest):
    """
    Analyze the brazing cycle using AI (Ollama).
    Specifically focuses on the temperature difference between Job 1 and Job 2.
    """
    try:
        print(f"Received cycle analysis request for {len(request.stages)} stages")
        analysis = analyze_temperature_difference(request.stages)
        return {
            "status": "success",
            "analysis": analysis
        }
    except Exception as e:
        print(f"Error during cycle analysis: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
def load_cad_to_trimesh(file_path: Path, default_alloy: str) -> Dict:
    """Load a CAD file and extract mesh and properties."""
    try:
        # Import Open3D here to avoid making it a hard dependency if not needed
        try:
            import open3d as o3d
        except ImportError:
            print("Warning: Open3D not available, using fallback methods for mesh generation")
            o3d = None
            
        if not file_path.exists():
            raise ValueError(f"File not found: {file_path}")
            
        # Check file extension
        file_ext = file_path.suffix.lower()
        
        # Handle IGES files
        if file_ext in ['.igs', '.iges']:
            try:
                # Try using pyiges to read the file
                import pyiges
                import numpy as np
                from pathlib import Path
                
                print(f"Attempting to read IGES file: {file_path}")
                
                # First, let's try to get the file size to check if it's not empty
                file_size = Path(file_path).stat().st_size
                print(f"File size: {file_size} bytes")
                if file_size == 0:
                    raise ValueError("IGES file is empty")
                
                # Read the first few lines to verify it's an IGES file
                try:
                    with open(file_path, 'r') as f:
                        first_lines = [next(f).strip() for _ in range(5)]
                        print("First few lines of IGES file:", first_lines)
                        if not any('IGES' in line for line in first_lines):
                            raise ValueError("File does not appear to be a valid IGES file")
                except Exception as e:
                    print(f"Warning: Could not read file content: {str(e)}")
                
                # Now try to parse with pyiges
                try:
                    iges = pyiges.read(file_path)
                    print("Successfully parsed IGES file")
                    
                    # Helper function to safely get geometry
                    def get_geometry(iges_obj, attr_name):
                        try:
                            attr = getattr(iges_obj, attr_name, None)
                            if attr is None:
                                return []
                            # If it's a method, call it
                            if callable(attr):
                                return attr() or []
                            # If it's a list/sequence, return it
                            if hasattr(attr, '__iter__') and not isinstance(attr, str):
                                return list(attr)
                            return []
                        except Exception as e:
                            print(f"Warning: Could not get {attr_name}: {str(e)}")
                            return []
                    
                    # Print available geometry types
                    print("\nAvailable geometry in IGES file:")
                    
                    # Get all geometry types
                    geometry_types = [
                        'points', 'lines', 'circular_arcs', 'bsplines',
                        'bspline_surfaces', 'conic_arcs', 'faces',
                        'edge_lists', 'vertex_lists', 'loops'
                    ]
                    
                    geometry_counts = {}
                    for geom_type in geometry_types:
                        geom_list = get_geometry(iges, geom_type)
                        geometry_counts[geom_type] = len(geom_list)
                        print(f"- {geom_type.replace('_', ' ').title()}: {len(geom_list)}")
                    print()  # Add a newline for better readability
                    
                    # Try to extract points from different geometry types
                    points = []
                    
                    # 1. Try to get points directly
                    point_entities = get_geometry(iges, 'points')
                    if point_entities:
                        print(f"Found {len(point_entities)} point entities")
                        for point in point_entities:
                            try:
                                if hasattr(point, 'X') and hasattr(point, 'Y') and hasattr(point, 'Z'):
                                    points.append([point.X, point.Y, point.Z])
                            except Exception as e:
                                print(f"Warning: Could not process point: {str(e)}")
                                continue
                    
                    # 2. Try to get points from lines
                    line_entities = get_geometry(iges, 'lines')
                    if line_entities:
                        print(f"Found {len(line_entities)} line entities")
                        for line in line_entities:
                            try:
                                # Try different ways to get points from lines
                                if hasattr(line, 'start_point') and hasattr(line, 'end_point'):
                                    # Get start and end points
                                    if hasattr(line.start_point, 'X') and hasattr(line.start_point, 'Y') and hasattr(line.start_point, 'Z'):
                                        points.append([line.start_point.X, line.start_point.Y, line.start_point.Z])
                                    if hasattr(line.end_point, 'X') and hasattr(line.end_point, 'Y') and hasattr(line.end_point, 'Z'):
                                        points.append([line.end_point.X, line.end_point.Y, line.end_point.Z])
                                
                                # Try to get points using the points() method if it exists
                                if hasattr(line, 'points') and callable(line.points):
                                    try:
                                        line_points = line.points()
                                        if line_points:
                                            for p in line_points:
                                                if hasattr(p, 'X') and hasattr(p, 'Y') and hasattr(p, 'Z'):
                                                    points.append([p.X, p.Y, p.Z])
                                    except Exception as e:
                                        print(f"Warning: Could not get points from line: {str(e)}")
                                        
                            except Exception as e:
                                print(f"Warning: Could not process line: {str(e)}")
                                continue
                    
                    # 3. Try to get points from circular arcs
                    arc_entities = get_geometry(iges, 'circular_arcs')
                    if arc_entities:
                        print(f"Found {len(arc_entities)} circular arc entities")
                        for arc in arc_entities:
                            try:
                                if hasattr(arc, 'start_point') and hasattr(arc, 'end_point') and hasattr(arc, 'center'):
                                    points.append([arc.start_point.X, arc.start_point.Y, arc.start_point.Z])
                                    points.append([arc.end_point.X, arc.end_point.Y, arc.end_point.Z])
                                    points.append([arc.center.X, arc.center.Y, arc.center.Z])
                            except Exception as e:
                                print(f"Warning: Could not process arc: {str(e)}")
                                continue
                    
                    # 4. Try to get points from B-splines
                    spline_entities = get_geometry(iges, 'bsplines')
                    if spline_entities:
                        print(f"Found {len(spline_entities)} B-spline entities")
                        for spline in spline_entities:
                            try:
                                # Get control points
                                if hasattr(spline, 'control_points'):
                                    cps = spline.control_points
                                    if callable(cps):
                                        cps = cps()
                                    for cp in cps or []:
                                        if hasattr(cp, 'X') and hasattr(cp, 'Y') and hasattr(cp, 'Z'):
                                            points.append([cp.X, cp.Y, cp.Z])
                                
                                # Try to evaluate points along the spline
                                if hasattr(spline, 'evaluate') and callable(spline.evaluate):
                                    try:
                                        # Evaluate at several points along the spline
                                        for t in [0.0, 0.25, 0.5, 0.75, 1.0]:
                                            pt = spline.evaluate(t)
                                            if pt and len(pt) >= 3:
                                                points.append([float(pt[0]), float(pt[1]), float(pt[2])])
                                    except Exception as e:
                                        print(f"Warning: Could not evaluate B-spline: {str(e)}")
                                        
                            except Exception as e:
                                print(f"Warning: Could not process B-spline: {str(e)}")
                                continue
                    
                    # 5. Try to get points from B-spline surfaces with simpler approach
                    surface_entities = get_geometry(iges, 'bspline_surfaces')
                    if surface_entities:
                        print(f"Found {len(surface_entities)} B-spline surface entities")
                        for i, surface in enumerate(surface_entities):
                            try:
                                # Try to get control points directly
                                cps = []
                                if hasattr(surface, 'control_points'):
                                    cp_data = surface.control_points
                                    if callable(cp_data):
                                        cp_data = cp_data()
                                    
                                    if cp_data is not None:
                                        # Handle different possible structures of control points
                                        try:
                                            # Try to iterate through control points
                                            for row in cp_data:
                                                row_points = []
                                                for cp in row:
                                                    # Handle different point representations
                                                    if hasattr(cp, 'X') and hasattr(cp, 'Y') and hasattr(cp, 'Z'):
                                                        row_points.append([float(cp.X), float(cp.Y), float(cp.Z)])
                                                    elif hasattr(cp, 'x') and hasattr(cp, 'y') and hasattr(cp, 'z'):
                                                        row_points.append([float(cp.x), float(cp.y), float(cp.z)])
                                                    elif isinstance(cp, (list, tuple)) and len(cp) >= 3:
                                                        row_points.append([float(cp[0]), float(cp[1]), float(cp[2])])
                                                
                                                if row_points:
                                                    cps.extend(row_points)
                                            
                                            print(f"Extracted {len(cps)} control points from B-spline surface {i+1}")
                                            points.extend(cps)
                                            
                                        except Exception as cp_error:
                                            print(f"Warning: Could not process control points for surface {i+1}: {str(cp_error)}")
                                
                                # Try to sample the surface directly if possible
                                try:
                                    if hasattr(surface, 'evaluate') and callable(surface.evaluate):
                                        sample_points = []
                                        for u in [0.0, 0.5, 1.0]:
                                            for v in [0.0, 0.5, 1.0]:
                                                try:
                                                    pt = surface.evaluate(u, v)
                                                    if pt is not None:
                                                        if hasattr(pt, 'X') and hasattr(pt, 'Y') and hasattr(pt, 'Z'):
                                                            sample_points.append([float(pt.X), float(pt.Y), float(pt.Z)])
                                                        elif isinstance(pt, (list, tuple)) and len(pt) >= 3:
                                                            sample_points.append([float(pt[0]), float(pt[1]), float(pt[2])])
                                                except Exception as eval_error:
                                                    print(f"Warning: Could not evaluate surface {i+1} at u={u}, v={v}: {str(eval_error)}")
                                        
                                        if sample_points:
                                            print(f"Sampled {len(sample_points)} points from B-spline surface {i+1}")
                                            points.extend(sample_points)
                                            
                                except Exception as eval_error:
                                    print(f"Warning: Could not sample surface {i+1}: {str(eval_error)}")
                                
                            except Exception as e:
                                print(f"Warning: Could not process B-spline surface {i+1}: {str(e)}")
                            
                            # Add some debug information about the surface
                            try:
                                print(f"Surface {i+1} type: {type(surface)}")
                                print(f"Surface {i+1} attributes: {[a for a in dir(surface) if not a.startswith('_')]}")
                            except:
                                pass
                    # Try to get points from B-spline curves as well
                    spline_entities = get_geometry(iges, 'bsplines')
                    if spline_entities:
                        print(f"Found {len(spline_entities)} B-spline curve entities")
                        for i, spline in enumerate(spline_entities):
                            try:
                                # Try to get control points
                                if hasattr(spline, 'control_points'):
                                    cp_data = spline.control_points
                                    if callable(cp_data):
                                        cp_data = cp_data()
                                    
                                    if cp_data is not None:
                                        for cp in cp_data:
                                            if hasattr(cp, 'X') and hasattr(cp, 'Y') and hasattr(cp, 'Z'):
                                                points.append([float(cp.X), float(cp.Y), float(cp.Z)])
                                            elif hasattr(cp, 'x') and hasattr(cp, 'y') and hasattr(cp, 'z'):
                                                points.append([float(cp.x), float(cp.y), float(cp.z)])
                                            elif isinstance(cp, (list, tuple)) and len(cp) >= 3:
                                                points.append([float(cp[0]), float(cp[1]), float(cp[2])])
                                
                                # Try to evaluate points along the curve
                                if hasattr(spline, 'evaluate') and callable(spline.evaluate):
                                    for t in [0.0, 0.25, 0.5, 0.75, 1.0]:
                                        try:
                                            pt = spline.evaluate(t)
                                            if pt is not None:
                                                if hasattr(pt, 'X') and hasattr(pt, 'Y') and hasattr(pt, 'Z'):
                                                    points.append([float(pt.X), float(pt.Y), float(pt.Z)])
                                                elif isinstance(pt, (list, tuple)) and len(pt) >= 3:
                                                    points.append([float(pt[0]), float(pt[1]), float(pt[2])])
                                        except Exception as e:
                                            print(f"Warning: Could not evaluate B-spline {i+1} at t={t}: {str(e)}")
                            
                            except Exception as e:
                                print(f"Warning: Could not process B-spline curve {i+1}: {str(e)}")
                    
                    print(f"Extracted {len(points)} points from IGES geometry")
                    
                    # If we still don't have points, try to use pyvista for export
                    if not points and hasattr(iges, 'to_pyvista'):
                        print("No points found in standard geometry, trying PyVista export...")
                        try:
                            import pyvista as pv
                            import numpy as np
                            
                            # Convert IGES to pyvista
                            mesh = iges.to_pyvista()
                            
                            if mesh is not None:
                                # Extract points from the mesh
                                if hasattr(mesh, 'points'):
                                    points = mesh.points.tolist()
                                    print(f"Extracted {len(points)} points using PyVista")
                                
                                # If no points, try to sample the surface
                                if not points and hasattr(mesh, 'sample'):
                                    sampled = mesh.sample(1000)  # Sample 1000 points
                                    if hasattr(sampled, 'points'):
                                        points = sampled.points.tolist()
                                        print(f"Sampled {len(points)} points from surface")
                            
                        except Exception as e:
                            print(f"PyVista export failed: {str(e)}")
                            print(f"VTK export failed: {str(e)}")
                    
                    # If we have points, create a mesh or point cloud
                    if points:
                        points = np.array(points, dtype=np.float64)
                        print(f"Created point array with shape: {points.shape}")
                        
                        # Remove duplicate points
                        points = np.unique(points, axis=0)
                        print(f"After removing duplicates: {len(points)} points")
                        
                        # Try to create a mesh using ball pivoting if we have enough points
                        if len(points) > 10:  # Need enough points for meaningful meshing
                            try:
                                # First try ball pivoting for better mesh reconstruction
                                print("Attempting to create mesh using ball pivoting...")
                                pcd = o3d.geometry.PointCloud()
                                pcd.points = o3d.utility.Vector3dVector(points)
                                
                                # Estimate normals (required for ball pivoting)
                                pcd.estimate_normals()
                                pcd.orient_normals_consistent_tangent_plane(100)
                                
                                # Ball pivoting reconstruction
                                radii = [0.1, 0.2, 0.4, 0.8]
                                mesh = o3d.geometry.TriangleMesh.create_from_point_cloud_ball_pivoting(
                                    pcd, o3d.utility.DoubleVector(radii))
                                
                                # Convert to trimesh format
                                vertices = np.asarray(mesh.vertices)
                                faces = np.asarray(mesh.triangles)
                                
                                if len(vertices) > 0 and len(faces) > 0:
                                    print(f"Created mesh with {len(vertices)} vertices and {len(faces)} faces using ball pivoting")
                                    return trimesh.Trimesh(vertices=vertices, faces=faces)
                                
                            except Exception as e:
                                print(f"Ball pivoting failed: {str(e)}")
                        
                        # Fall back to convex hull if we have enough points
                        if len(points) > 3:
                            try:
                                from scipy.spatial import ConvexHull, Delaunay
                                print("Falling back to convex hull...")
                                
                                # First try Delaunay triangulation for 3D points
                                try:
                                    print("Attempting Delaunay triangulation...")
                                    tri = Delaunay(points)
                                    # Get the convex hull simplices
                                    hull = ConvexHull(points)
                                    mesh = trimesh.Trimesh(vertices=points, faces=hull.simplices)
                                    print(f"Created mesh with {len(points)} vertices and {len(hull.simplices)} faces using convex hull")
                                    return mesh
                                except Exception as e:
                                    print(f"Delaunay triangulation failed: {str(e)}")
                                    
                                    # If that fails, try simple convex hull
                                    try:
                                        hull = ConvexHull(points)
                                        mesh = trimesh.Trimesh(vertices=points, faces=hull.simplices)
                                        print(f"Created mesh with {len(points)} vertices and {len(hull.simplices)} faces using convex hull")
                                        return mesh
                                    except Exception as e:
                                        print(f"Convex hull failed: {str(e)}")
                                        
                            except Exception as e:
                                print(f"Could not create mesh from points: {str(e)}")
                        
                        # If all else fails, create a point cloud
                        print("Falling back to point cloud representation")
                        return trimesh.PointCloud(vertices=points)
                    
                    else:
                        raise ValueError("No geometry data could be extracted from the IGES file")
                        
                except Exception as e:
                    raise ValueError(f"Failed to process IGES file: {str(e)}")
                
            except Exception as e:
                raise ValueError(f"Failed to process IGES file: {str(e)}")
                    
            except Exception as e:
                raise ValueError(f"Failed to process IGES file: {str(e)}")
        else:
            # For non-IGES files, try direct loading
            mesh = trimesh.load(file_path)
        
        # Validate the loaded mesh
        if not hasattr(mesh, 'vertices') or not hasattr(mesh, 'faces'):
            raise ValueError(f"Invalid mesh data in {file_path}. Make sure the file contains 3D geometry.")
            
        if len(mesh.vertices) == 0 or len(mesh.faces) == 0:
            raise ValueError(f"Empty mesh in {file_path}. The file must contain 3D geometry data.")
        
        # Get bounding box dimensions
        bbox = mesh.bounding_box.bounds
        dimensions = mesh.extents
        
        return {
            "mesh": mesh,
            "volume": mesh.volume,
            "surface_area": mesh.area,
            "dimensions": dimensions.tolist(),
            "bounding_box": bbox.tolist(),
            "alloy": default_alloy,
            "material_properties": MATERIAL_DB[default_alloy]
        }
    except Exception as e:
        raise ValueError(f"Failed to load {file_path}: {str(e)}")

def simplify_mesh(mesh, target_vertices=5000):
    """
    Simplify a mesh to a target number of vertices using Open3D.
    
    Args:
        mesh: Input mesh (trimesh.Trimesh or point cloud)
        target_vertices: Maximum number of vertices in the simplified mesh
        
    Returns:
        Simplified trimesh.Trimesh object
    """
    try:
        import open3d as o3d
        
        print(f"Simplifying mesh from {len(mesh.vertices)} to max {target_vertices} vertices...")
        
        # Convert to Open3D point cloud
        pcd = o3d.geometry.PointCloud()
        pcd.points = o3d.utility.Vector3dVector(mesh.vertices)
        
        # Preserve colors if they exist
        if hasattr(mesh, 'visual') and hasattr(mesh.visual, 'vertex_colors') and len(mesh.visual.vertex_colors) > 0:
            pcd.colors = o3d.utility.Vector3dVector(mesh.visual.vertex_colors[:, :3] / 255.0)
        
        # If already below target, return as is
        if len(pcd.points) <= target_vertices:
            print(f"Mesh already has {len(pcd.points)} vertices, no simplification needed")
            return mesh
        
        # First pass: Voxel downsampling with adaptive voxel size
        voxel_size = 0.01  # Start with 1cm voxels
        max_iterations = 30
        iteration = 0
        last_point_count = len(pcd.points)
        
        while len(pcd.points) > target_vertices * 1.5 and voxel_size < 10.0 and iteration < max_iterations:
            pcd_downsampled = pcd.voxel_down_sample(voxel_size)
            new_point_count = len(pcd_downsampled.points)
            
            # Check if downsampling is making progress
            if new_point_count >= last_point_count * 0.95:
                # Not much progress, increase voxel size more aggressively
                voxel_size *= 3.0
                print(f"Voxel size {voxel_size/3:.4f} not effective, trying {voxel_size:.4f}")
            else:
                # Good progress, keep this downsampled version
                pcd = pcd_downsampled
                last_point_count = new_point_count
                voxel_size *= 1.5  # Increase voxel size for next iteration
                print(f"Voxel downsampled to {new_point_count} points with voxel size {voxel_size/1.5:.4f}")
            
            iteration += 1
        
        # Second pass: Random sampling if still too many points
        if len(pcd.points) > target_vertices:
            indices = np.random.choice(len(pcd.points), size=target_vertices, replace=False)
            pcd = pcd.select_by_index(indices)
            print(f"Randomly sampled to {len(pcd.points)} points")
        
        # Convert back to trimesh
        vertices = np.asarray(pcd.points)
        
        # Create a new trimesh object
        if hasattr(mesh, 'faces') and len(mesh.faces) > 0:
            # For meshes, create a simplified mesh using vertex clustering
            simplified = trimesh.Trimesh(vertices=vertices)
            # Try to preserve normals if they exist
            if hasattr(mesh, 'vertex_normals') and len(mesh.vertex_normals) > 0:
                simplified.vertex_normals = mesh.vertex_normals[:len(vertices)]
            # Try to preserve colors if they exist
            if hasattr(mesh, 'visual') and hasattr(mesh.visual, 'vertex_colors') and len(mesh.visual.vertex_colors) > 0:
                if pcd.has_colors():
                    simplified.visual.vertex_colors = (np.asarray(pcd.colors) * 255).astype(np.uint8)
        else:
            # For point clouds, create a new point cloud
            simplified = trimesh.PointCloud(vertices=vertices)
            if pcd.has_colors():
                simplified.colors = (np.asarray(pcd.colors) * 255).astype(np.uint8)
        
        print(f"Simplified mesh from {len(mesh.vertices)} to {len(vertices)} vertices")
        return simplified
        
    except Exception as e:
        print(f"Error simplifying mesh: {str(e)}")
        # Fallback to random sampling
        if len(mesh.vertices) > target_vertices:
            print("Falling back to random sampling...")
            indices = np.random.choice(len(mesh.vertices), size=target_vertices, replace=False)
            return mesh.submesh([indices])[0]
        return mesh

def combine_meshes(meshes: Dict, max_vertices_per_mesh=5000, max_total_vertices=10000) -> Dict:
    """
    Combine multiple meshes or point clouds into a single mesh for simulation.
    Handles both trimesh objects and point clouds with proper type checking and error handling.
    
    Args:
        meshes: Dictionary of meshes to combine
        max_vertices_per_mesh: Maximum number of vertices per mesh before simplification
        max_total_vertices: Maximum total number of vertices in the combined mesh
        
    Returns:
        Dictionary containing combined vertices and faces
    """
    if not meshes:
        print("Warning: No meshes to combine")
        return {}
        
    combined_vertices = []
    combined_faces = []
    vertex_offset = 0
    total_vertices = 0
    
    print(f"Combining {len(meshes)} meshes...")
    
    # First, validate all meshes and collect vertices/faces
    for name, data in meshes.items():
        # Check if data is None or not a dictionary
        if data is None or not isinstance(data, dict):
            print(f"Warning: Invalid mesh data for {name}, skipping")
            continue
        
        # Try to get mesh from different possible locations
        mesh = data.get("mesh")
        if mesh is None:
            # Try to get from properties if it's stored there
            properties = data.get("properties", {})
            if isinstance(properties, dict):
                mesh = properties.get("mesh")
        
        if mesh is None:
            print(f"Warning: No mesh found for {name}, skipping")
            continue
            
        try:
            # Convert to numpy array if it's a list
            if isinstance(mesh, list):
                mesh = np.array(mesh)
            
            # Convert to trimesh object if it's a numpy array
            if isinstance(mesh, np.ndarray):
                if len(mesh.shape) == 1 and len(mesh) >= 3:  # Single point
                    mesh = mesh.reshape(1, -1)
                if len(mesh.shape) == 2 and mesh.shape[1] >= 3:  # Point cloud
                    mesh = trimesh.PointCloud(vertices=mesh[:, :3])
            
            # Simplify mesh if it's too large
            if hasattr(mesh, 'vertices') and len(mesh.vertices) > max_vertices_per_mesh:
                print(f"Mesh {name} has {len(mesh.vertices)} vertices, simplifying...")
                mesh = simplify_mesh(mesh, max_vertices_per_mesh)
            
            # Handle different mesh types
            if isinstance(mesh, trimesh.Trimesh) or (hasattr(mesh, 'vertices') and hasattr(mesh, 'faces')):
                # Standard trimesh object
                vertices = np.array(mesh.vertices, dtype=np.float64)
                
                # Skip if this would exceed our vertex budget
                if total_vertices + len(vertices) > max_total_vertices * 1.5:  # Allow some overflow
                    print(f"Warning: Reached vertex limit, skipping remaining meshes")
                    break
                    
                combined_vertices.append(vertices)
                
                if hasattr(mesh, 'faces') and len(mesh.faces) > 0:
                    faces = np.array(mesh.faces, dtype=np.int64) + vertex_offset
                    combined_faces.append(faces)
                    
                vertex_offset += len(vertices)
                total_vertices += len(vertices)
                print(f"Added {name}: {len(vertices)} vertices, {len(mesh.faces) if hasattr(mesh, 'faces') else 0} faces (total: {total_vertices} vertices)")
                
            elif isinstance(mesh, trimesh.PointCloud) or (hasattr(mesh, 'vertices') and not hasattr(mesh, 'faces')):
                # Point cloud
                vertices = np.array(mesh.vertices, dtype=np.float64)
                
                # Skip if this would exceed our vertex budget
                if total_vertices + len(vertices) > max_total_vertices * 1.5:  # Allow some overflow
                    print(f"Warning: Reached vertex limit, skipping remaining meshes")
                    break
                    
                combined_vertices.append(vertices)
                vertex_offset += len(vertices)
                total_vertices += len(vertices)
                print(f"Added {name} (point cloud): {len(vertices)} points (total: {total_vertices} vertices)")
                
            elif isinstance(mesh, (np.ndarray, list, tuple)):
                # Raw point cloud data
                points = np.array(mesh, dtype=np.float64)
                if len(points) == 0:
                    print(f"Warning: Empty point cloud for {name}, skipping")
                    continue
                    
                # Ensure it's a 2D array (N,3)
                if len(points.shape) == 1:
                    if len(points) >= 3:  # Single point
                        points = points.reshape(1, -1)
                    else:
                        print(f"Warning: Not enough points for {name}, need at least 3 coordinates")
                        continue
                        
                # If we have at least 3D points
                if points.shape[1] >= 3:
                    # Take only x,y,z coordinates if more dimensions exist
                    points = points[:, :3]
                    
                    # Skip if this would exceed our vertex budget
                    if total_vertices + len(points) > max_total_vertices * 1.5:  # Allow some overflow
                        print(f"Warning: Reached vertex limit, skipping remaining meshes")
                        break
                        
                    combined_vertices.append(points)
                    vertex_offset += len(points)
                    total_vertices += len(points)
                    print(f"Added {name} (raw points): {len(points)} points (total: {total_vertices} vertices)")
                    
        except Exception as e:
            print(f"Error processing mesh {name}: {str(e)}")
            print(f"Mesh type: {type(mesh)}, has vertices: {hasattr(mesh, 'vertices') if hasattr(mesh, 'vertices') else 'N/A'}, has faces: {hasattr(mesh, 'faces') if hasattr(mesh, 'faces') else 'N/A'}")
            import traceback
            traceback.print_exc()
            continue
    
    # Combine all vertices and faces
    result = {}
    
    # Check if we have any vertices to combine
    if not combined_vertices:
        print("Error: No valid meshes found to combine")
        return {}
        
    try:
        # Combine all vertices
        print(f"Combining {len(combined_vertices)} vertex arrays...")
        all_vertices = np.vstack(combined_vertices)
        
        # If we have too many vertices, do a final simplification
        if len(all_vertices) > max_total_vertices:
            print(f"Final simplification: reducing from {len(all_vertices)} to {max_total_vertices} vertices...")
            pcd = trimesh.PointCloud(vertices=all_vertices)
            simplified = simplify_mesh(pcd, max_total_vertices)
            all_vertices = simplified.vertices
            
            # If we had faces, we need to regenerate them
            if combined_faces:
                print("Warning: Faces may not be valid after final simplification")
                combined_faces = []
        
        # Create a proper Trimesh object with faces if we have them
        if combined_faces and len(combined_faces) > 0:
            all_faces = np.vstack(combined_faces)
            # Ensure all face indices are within bounds
            max_vertex_index = len(all_vertices) - 1
            if np.any(all_faces > max_vertex_index):
                print("Warning: Face indices out of bounds. Truncating to valid range.")
                all_faces = np.clip(all_faces, 0, max_vertex_index)
            
            # Create a proper trimesh object
            mesh = trimesh.Trimesh(vertices=all_vertices, faces=all_faces, process=True)
            
            # If the mesh is not watertight, try to fix it
            if not mesh.is_watertight:
                print("Mesh is not watertight, attempting to repair...")
                trimesh.repair.fix_normals(mesh)
                trimesh.repair.fill_holes(mesh)
                
                # If still not watertight, try to make it manifold
                if not mesh.is_watertight:
                    print("Mesh could not be made watertight, using convex hull...")
                    mesh = mesh.convex_hull
            
            print(f"Created watertight mesh with {len(mesh.vertices)} vertices and {len(mesh.faces)} faces")
            return {
                "vertices": mesh.vertices,
                "faces": mesh.faces,
                "is_watertight": mesh.is_watertight
            }
        else:
            # No faces from components: fall back to convex-hull approximation of all vertices
            print(f"No faces found in combined mesh; building convex-hull approximation from {len(all_vertices)} points...")
            try:
                pcd = trimesh.PointCloud(vertices=all_vertices)
                hull = pcd.convex_hull
                print(f"Created convex hull with {len(hull.vertices)} vertices and {len(hull.faces)} faces")
                return {
                    "vertices": hull.vertices,
                    "faces": hull.faces,
                    "is_watertight": hull.is_watertight,
                    "is_convex_hull": True
                }
            except Exception as e:
                print(f"Could not create convex hull from combined vertices: {e}")
                print("Falling back to raw point cloud for combined geometry")
                return {
                    "vertices": all_vertices,
                    "faces": np.zeros((0, 3), dtype=np.int64),
                    "is_watertight": False,
                    "is_point_cloud": True
                }
            
    except Exception as e:
        print(f"Error combining meshes: {str(e)}")
        import traceback
        traceback.print_exc()
        return {}

def compute_target_braze_temp(filler_alloy: str, base_alloys: List[str]) -> Dict:
    """Compute safe brazing temperature range based on material properties."""
    filler = MATERIAL_DB[filler_alloy]
    base_solidus = min(MATERIAL_DB[a]["solidus"] for a in base_alloys)
    
    # Safe range: between filler liquidus and base solidus
    target_temp = min(filler["liquidus"] + 10, base_solidus - 10)
    
    return {
        "min_temp": filler["liquidus"],
        "max_temp": base_solidus,
        "target_temp": target_temp,
        "recommended_dwell_time": 30,  # seconds
        "recommended_heat_rate": 10    # °C/s
    }

def calculate_heat_diffusion(vertices, temperature_field, thermal_conductivity, specific_heat, density, time_step, heat_source_temp, heat_source_radius):
    """
    Calculate heat diffusion using simplified finite difference method.
    
    Args:
        vertices: Mesh vertices (N, 3)
        temperature_field: Current temperature at each vertex
        thermal_conductivity: Material thermal conductivity (W/(m·K))
        specific_heat: Material specific heat (J/(kg·K))
        density: Material density (kg/m³)
        time_step: Time step for simulation (s)
        heat_source_temp: Temperature of heat source (°C)
        heat_source_radius: Radius of heat source (m)
    
    Returns:
        Updated temperature field
    """
    try:
        # Debug information
        print(f"Temperature field type: {type(temperature_field)}")
        if hasattr(temperature_field, 'shape'):
            print(f"Temperature field shape: {temperature_field.shape}")
        
        # If temperature_field is a dictionary, extract the values
        if isinstance(temperature_field, dict):
            print("Temperature field is a dictionary, extracting values...")
            temperature_field = np.array(list(temperature_field.values()), dtype=np.float32)
        # Ensure temperature_field is a numpy array of floats
        elif not isinstance(temperature_field, np.ndarray):
            temperature_field = np.array(temperature_field, dtype=np.float32)
            
        print(f"Vertices type: {type(vertices)}")
        if hasattr(vertices, 'shape'):
            print(f"Vertices shape: {vertices.shape}")
        elif hasattr(vertices, '__len__'):
            print(f"Vertices length: {len(vertices)}")
        else:
            temperature_field = temperature_field.astype(np.float32)
        
        # Ensure vertices is a numpy array
        if not isinstance(vertices, np.ndarray):
            vertices = np.array(vertices, dtype=np.float32)
        else:
            vertices = vertices.astype(np.float32)
        
        new_temp = temperature_field.copy()
        
        # Validate inputs
        if len(vertices) == 0 or len(temperature_field) == 0:
            return new_temp
        
        # Calculate thermal diffusivity (m²/s)
        thermal_diffusivity = float(thermal_conductivity) / (float(specific_heat) * float(density))
        
        # Heat source location (center of mesh)
        mesh_center = np.mean(vertices, axis=0)
        
        # Calculate distances from each vertex to heat source
        distances = np.linalg.norm(vertices - mesh_center, axis=1)
        
        # Apply heat source (Gaussian distribution)
        heat_influence = np.exp(-(distances ** 2) / (2 * float(heat_source_radius) ** 2))
        heat_source_effect = heat_influence * (float(heat_source_temp) - temperature_field) * 0.1  # Increased from 0.05 to 0.1
        
        # Build a KDTree for efficient neighbor searches
        from scipy.spatial import KDTree
        tree = KDTree(vertices)
        
        # For each vertex, find neighbors and compute heat diffusion
        for i in range(min(len(vertices), len(temperature_field))):
            # Find nearby vertices (within 1cm)
            neighbor_indices = tree.query_ball_point(vertices[i], r=0.01)  # 1cm radius
            
            # Remove self from neighbors
            neighbor_indices = [idx for idx in neighbor_indices if idx != i]
            
            if neighbor_indices:
                # Calculate average temperature of neighbors
                avg_neighbor_temp = np.mean(temperature_field[neighbor_indices])
                
                # Calculate temperature gradient
                temp_gradient = avg_neighbor_temp - temperature_field[i]
                
                # Apply diffusion based on thermal diffusivity and time step
                diffusion_effect = temp_gradient * thermal_diffusivity * float(time_step) * 100  # Increased from 0.1 to 100
                new_temp[i] += diffusion_effect
        
        # Apply heat source effect (after diffusion for stability)
        new_temp = new_temp + heat_source_effect
        
        # Ambient cooling (Newton's law of cooling) - only apply to surface vertices
        # For simplicity, we'll apply it to all vertices but with a small effect
        ambient_temp = 25.0
        cooling_rate = 0.05  # Increased from 0.01 to 0.05
        cooling_effect = (ambient_temp - new_temp) * cooling_rate * float(time_step)
        new_temp = new_temp + cooling_effect
        
        # Clamp temperatures to reasonable values
        new_temp = np.clip(new_temp, 25.0, float(heat_source_temp * 1.1))  # Allow slight overshoot
        
        return new_temp
        
    except Exception as e:
        print(f"Error in calculate_heat_diffusion: {str(e)}")
        print(f"Temperature field type: {type(temperature_field)}, shape: {getattr(temperature_field, 'shape', 'N/A')}")
        print(f"Vertices type: {type(vertices)}, shape: {getattr(vertices, 'shape', 'N/A')}")
        traceback.print_exc()
        # Return original temperature field on error
        return np.array(temperature_field, dtype=np.float32)

@app.websocket("/ws/sim")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time FEM-based heat spreading simulation."""
    await websocket.accept()
    
    if not hasattr(simulation, 'simulation_running'):
        simulation.simulation_running = True
        
    print(f"WebSocket connection established. Simulation running: {getattr(simulation, 'simulation_running', False)}")
    
    def log_websocket_error(message: str, error: Exception = None):
        error_msg = f"WebSocket Error: {message}"
        if error:
            error_msg += f"\n{str(error)}\n{traceback.format_exc()}"
        print(error_msg)
        return error_msg
    
    # Simulation parameters
    total_simulation_time = 120.0  # Total simulation time in seconds
    time_step = 0.1  # Time step for each iteration (100ms)
    last_update_time = time.time()
    update_interval = 0.2  # Send updates every 200ms for smoother updates
    last_send_time = time.time()
    
    try:
        # Check if we have FEM simulator
        if not hasattr(simulation, 'fem_simulator') or simulation.fem_simulator is None:
            await websocket.send_json({
                "error": "No FEM simulator initialized. Please upload files first.",
                "simulation_running": False
            })
            return
        
        # Reset simulator for new run
        simulation.fem_simulator.reset()
        
        # Check if we have a mesh (fallback)
        if not hasattr(simulation, 'combined_mesh') or simulation.combined_mesh is None:
            await websocket.send_json({
                "error": "No mesh loaded. Please upload files first.",
                "simulation_running": False
            })
            return
        
        # Initialize temperature field if needed
        if not hasattr(simulation, 'temperature_field') or simulation.temperature_field is None:
            if hasattr(simulation.combined_mesh, 'vertices'):
                n_vertices = len(simulation.combined_mesh.vertices)
                simulation.temperature_field = np.full(n_vertices, 25.0, dtype=np.float32)
                print(f"Initialized temperature field with {n_vertices} vertices")
            else:
                await websocket.send_json({
                    "error": "Mesh has no vertices",
                    "simulation_running": False
                })
                return
        
        # Get material properties
        material_props = None
        for part_name, mesh_data in simulation.meshes.items():
            if 'material' in mesh_data:
                material_props = mesh_data['material']
                break
        
        if not material_props:
            material_props = MATERIAL_DB["6061-T6"]
        
        thermal_conductivity = material_props.get('thermal_conductivity', 167)  # W/(m·K)
        specific_heat = material_props.get('specific_heat', 900)  # J/(kg·K)
        density = material_props.get('density', 2700)  # kg/m³
        
        # Heat source parameters
        heat_source_temp = simulation.target_temperature
        heat_source_radius = 0.01  # 10mm radius
        
        # Main simulation loop using FEM
        frame_count = 0
        while (simulation.simulation_running and 
               simulation.fem_simulator is not None and
               simulation.simulation_time < total_simulation_time):
            
            current_time = time.time()
            time_delta = current_time - last_update_time
            last_update_time = current_time
            
            # Update simulation time
            simulation.simulation_time = min(simulation.simulation_time + time_delta, total_simulation_time)
            progress = min(1.0, simulation.simulation_time / total_simulation_time)
            
            # Perform FEM heat simulation step
            try:
                # Skip FEM for large meshes to prevent hanging
                if len(simulation.combined_mesh.vertices) > 5000:
                    print(f"Mesh too large for FEM ({len(simulation.combined_mesh.vertices)} vertices), using basic simulation")
                    raise Exception("Large mesh, skip FEM")
                
                import asyncio
                
                # Run FEM simulation in a thread pool with timeout
                def run_fem_step():
                    return simulation.fem_simulator.step(time_step)
                
                # Use ThreadPoolExecutor to run FEM step with timeout
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(run_fem_step)
                    try:
                        # Timeout after 5 seconds to prevent hanging
                        sim_results = future.result(timeout=5.0)
                        print(f"FEM step completed in {time.time() - current_time:.3f}s")
                    except concurrent.futures.TimeoutError:
                        print("FEM simulation step timed out, falling back to basic simulation")
                        future.cancel()
                        raise Exception("FEM timeout")
                
                # Update simulation state with FEM results
                if 'temperature_field' in sim_results:
                    simulation.temperature_field = np.array(sim_results['temperature_field'])
                
                # Get enhanced visualization data (with timeout protection)
                try:
                    mesh_data = simulation.fem_simulator.get_mesh_for_visualization()
                    if mesh_data and 'temperature_field' in mesh_data:
                        # Only create enhanced viz if mesh is not too large
                        if len(mesh_data.get('vertices', [])) <= 5000:
                            enhanced_viz = simulation.visualizer.create_visualization_config(
                                mesh_data, 
                                mesh_data['temperature_field']
                            )
                            
                            # Store enhanced visualization data for frontend
                            sim_results['enhanced_viz'] = {
                                'heat_vectors': enhanced_viz['effects']['heat_vectors'],
                                'material_properties': enhanced_viz['materials'],
                                'shader_uniforms': enhanced_viz['shaders']['uniforms']
                            }
                except Exception as viz_error:
                    print(f"Visualization creation failed: {viz_error}")
                    # Continue without enhanced visualization
                
            except Exception as e:
                print(f"Error during FEM simulation step: {str(e)}")
                traceback.print_exc()
                # Fallback to basic simulation - use simplified approach for large meshes
                if hasattr(simulation.combined_mesh, 'vertices') and simulation.temperature_field is not None:
                    vertices = simulation.combined_mesh.vertices
                    n_points = min(len(vertices), len(simulation.temperature_field))
                    
                    if n_points > 0:
                        # For large meshes, use simplified diffusion model
                        if n_points > 5000:
                            print(f"Using simplified diffusion for {n_points} points")
                            # Simplified model: heat spreads from center
                            center = np.mean(vertices, axis=0)
                            distances = np.linalg.norm(vertices - center, axis=1)
                            max_distance = np.max(distances) if len(distances) > 0 else 1.0
                            
                            # Heat spreads radially from center
                            heat_factor = 1.0 - (distances / max_distance)
                            target_temps = heat_source_temp * heat_factor + 25.0 * (1 - heat_factor)
                            
                            # Smooth transition
                            simulation.temperature_field = simulation.temperature_field + \
                                (target_temps - simulation.temperature_field) * 0.1
                        else:
                            # Use detailed diffusion for smaller meshes
                            updated_temps = calculate_heat_diffusion(
                                vertices[:n_points],
                                simulation.temperature_field[:n_points],
                                thermal_conductivity,
                                specific_heat,
                                density,
                                time_step,
                                heat_source_temp,
                                heat_source_radius
                            )
                            
                            if updated_temps is not None and len(updated_temps) > 0:
                                simulation.temperature_field[:n_points] = updated_temps
                
                # Create basic results structure
                sim_results = {
                    'time': simulation.simulation_time,
                    'progress': progress * 100,
                    'temperature_field': simulation.temperature_field.tolist() if simulation.temperature_field is not None else [],
                    'max_temp': float(np.max(simulation.temperature_field)) if simulation.temperature_field is not None else 25.0,
                    'min_temp': float(np.min(simulation.temperature_field)) if simulation.temperature_field is not None else 25.0,
                    'avg_temp': float(np.mean(simulation.temperature_field)) if simulation.temperature_field is not None else 25.0,
                    'simulation_complete': progress >= 1.0
                }
            
            # Send updates at regular intervals (500ms)
            current_send_time = time.time()
            if (current_send_time - last_send_time) >= update_interval:
                last_send_time = current_send_time
                frame_count += 1
                
                # Use results from FEM simulation or fallback
                max_temp = sim_results.get('max_temp', 25.0)
                min_temp = sim_results.get('min_temp', 25.0)
                avg_temp = sim_results.get('avg_temp', 25.0)
                progress_percent = sim_results.get('progress', progress * 100)
                
                # Prepare enhanced data for frontend
                update_data = {
                    "type": "simulation_update",
                    "time": float(sim_results.get('time', simulation.simulation_time)),
                    "progress": min(100.0, progress_percent),
                    "temperature_field": sim_results.get('temperature_field', []),
                    "max_temp": max_temp,
                    "min_temp": min_temp,
                    "avg_temp": avg_temp,
                    "simulation_running": progress < 1.0,
                    "frame": frame_count
                }
                
                # Add enhanced visualization data if available
                if 'enhanced_viz' in sim_results:
                    update_data['enhanced_viz'] = sim_results['enhanced_viz']
                
                # Send progress update
                try:
                    await websocket.send_json(update_data)
                    
                    # If we've reached the end, send a final update and break
                    if progress >= 1.0:
                        await websocket.send_json({
                            "type": "simulation_complete",
                            "time": float(total_simulation_time),
                            "progress": 100.0,
                            "max_temp": max_temp,
                            "min_temp": min_temp,
                            "avg_temp": avg_temp,
                            "simulation_running": False,
                            "simulation_complete": True
                        })
                        break
                        
                except Exception as e:
                    print(f"Error sending WebSocket update: {e}")
                    break
            
            # Small sleep to prevent 100% CPU usage
            await asyncio.sleep(0.01)
            
    except WebSocketDisconnect:
        print("WebSocket disconnected by client")
    except Exception as e:
        print(f"WebSocket error: {e}")
    finally:
        simulation.simulation_running = False
        simulation.simulation_time = 0.0  # Reset for next run
        print("Simulation stopped")

@app.get("/api/mesh")
async def get_mesh():
    """Get the current mesh data for visualization."""
    if not hasattr(simulation, 'combined_mesh') or simulation.combined_mesh is None:
        raise HTTPException(status_code=404, detail="No mesh loaded. Please upload and process files first.")
    
    print(f"Combined mesh type: {type(simulation.combined_mesh)}")
    
    # Safely get temperature field
    temperature_field = None
    if hasattr(simulation, 'temperature_field') and simulation.temperature_field is not None:
        try:
            temperature_field = simulation.temperature_field.tolist()
        except AttributeError:
            temperature_field = None
    
    try:
        # Initialize response data
        mesh_data = {
            "vertices": [],
            "faces": [],
            "temperature_field": temperature_field,
            "materials": [],
            "mesh_type": "point_cloud"  # Default to point cloud
        }
        
        # Handle different mesh types
        if hasattr(simulation.combined_mesh, 'vertices'):
            mesh_data["vertices"] = simulation.combined_mesh.vertices.tolist()
            
            # Check if it's a trimesh with faces
            if hasattr(simulation.combined_mesh, 'faces'):
                mesh_data["faces"] = simulation.combined_mesh.faces.tolist()
                mesh_data["mesh_type"] = "mesh"
            # Check if it's a point cloud
            elif hasattr(simulation.combined_mesh, 'points'):
                mesh_data["vertices"] = simulation.combined_mesh.points.tolist()
                mesh_data["mesh_type"] = "point_cloud"
        
        # Add material information if available
        if hasattr(simulation, 'meshes'):
            mesh_data["materials"] = []
            for name, data in simulation.meshes.items():
                if isinstance(data, dict) and 'material' in data:
                    mesh_data["materials"].append({
                        "name": name,
                        "properties": data.get('material', {})
                    })
        
        print(f"Returning mesh data with {len(mesh_data['vertices'])} vertices and {len(mesh_data['faces'])} faces")
        return mesh_data
        
    except Exception as e:
        error_msg = f"Error preparing mesh data: {str(e)}"
        print(error_msg)
        print(f"Mesh data type: {type(simulation.combined_mesh)}")
        print(f"Available attributes: {dir(simulation.combined_mesh)}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=error_msg)

@app.get("/api/geometry/properties")
async def get_geometry_properties():
    """
    Get detailed geometric properties of all uploaded models.
    
    Returns:
        Dict containing geometric properties of all models
    """
    if not hasattr(simulation, 'meshes') or not simulation.meshes:
        return {"error": "No models have been uploaded yet"}
    
    result = {}
    
    # Process each uploaded model
    for part_type, mesh_info in simulation.meshes.items():
        try:
            if 'file_path' in mesh_info and Path(mesh_info['file_path']).exists():
                # Process the file to get detailed properties
                properties = process_iges_file(Path(mesh_info['file_path']), part_type)
                
                # Add material properties if available
                if 'material' in mesh_info:
                    properties['material'] = mesh_info['material']
                
                result[part_type] = properties
            else:
                result[part_type] = {
                    'error': 'File not found',
                    'file_path': mesh_info.get('file_path', 'Not specified')
                }
        except Exception as e:
            result[part_type] = {
                'error': f'Error processing {part_type}: {str(e)}',
                'file_path': mesh_info.get('file_path', 'Not specified')
            }
    
    return result

if __name__ == "__main__":
    # Create static directory if it doesn't exist
    os.makedirs("static", exist_ok=True)
    uvicorn.run("main:app", host="localhost", port=8085, reload=True)
