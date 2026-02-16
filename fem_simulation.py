"""
FEM-based Heat Transfer Simulation Module
Implements realistic heat flow simulation using FEniCS for vacuum brazing process
"""

import numpy as np
import trimesh
from typing import Dict, List, Tuple, Optional
import time
import logging

try:
    from dolfin import *
    # Try to import UserDefinedExpression (available in older FEniCS)
    try:
        from dolfin import UserDefinedExpression
        USER_DEFINED_EXPR_AVAILABLE = True
    except ImportError:
        USER_DEFINED_EXPR_AVAILABLE = False
        print("⚠️  UserDefinedExpression not available, using Expression instead")
    
    FENICS_AVAILABLE = True
    print("✓ FEniCS dolfin imported successfully")
except ImportError as e:
    print(f"⚠️  Warning: FEniCS not available. Install with: conda install -c conda-forge fenics-dolfin")
    print(f"   Import error: {e}")
    print("   Note: Use 'from dolfin import *' not 'import fenics' (fenics is just a meta package)")
    FENICS_AVAILABLE = False
    USER_DEFINED_EXPR_AVAILABLE = False

try:
    import gmsh
    GMSH_AVAILABLE = True
    print("✓ GMSH imported successfully for mesh generation")
except ImportError:
    print("⚠️  Warning: GMSH not available. Install with: conda install -c conda-forge gmsh")
    GMSH_AVAILABLE = False

try:
    import meshio
    MESHIO_AVAILABLE = True
except ImportError:
    print("⚠️  Warning: meshio not available for mesh conversion")
    MESHIO_AVAILABLE = False

try:
    import pyvista as pv
    PYVISTA_AVAILABLE = True
except ImportError:
    print("Warning: PyVista not available for advanced visualization")
    PYVISTA_AVAILABLE = False

class FEMHeatSimulation:
    """
    Advanced FEM-based heat transfer simulation for vacuum brazing
    """
    
    def __init__(self, mesh_data: Dict, material_properties: Dict):
        """
        Initialize FEM simulation
        
        Args:
            mesh_data: Combined mesh data with vertices and faces
            material_properties: Material thermal properties
        """
        self.mesh_data = mesh_data
        self.material_props = material_properties
        self.fem_mesh = None
        self.function_space = None
        self.temperature_field = None
        self.solution_history = []
        
        # Simulation parameters
        self.time_step = 0.1  # seconds
        self.total_time = 120.0  # seconds
        self.current_time = 0.0
        
        # Heat source parameters
        self.heat_source_temp = 600.0  # °C
        self.heat_source_radius = 0.01  # 10mm
        self.heat_source_power = 1000.0  # Watts
        
        # Boundary conditions
        self.ambient_temp = 25.0  # °C
        self.convection_coeff = 10.0  # W/(m²·K)
        self.emissivity = 0.8  # for radiation
        self.stefan_boltzmann = 5.67e-8  # W/(m²·K⁴)
        
        if FENICS_AVAILABLE:
            self._setup_fem_mesh()
        else:
            logging.warning("FEniCS not available, falling back to simplified simulation")
    
    def _setup_fem_mesh(self):
        """Create FEniCS mesh from trimesh data using GMSH"""
        try:
            vertices = np.array(self.mesh_data.get('vertices', []))
            faces = np.array(self.mesh_data.get('faces', []))

            if len(vertices) == 0:
                raise ValueError("No vertices in mesh data")

            # Try to create mesh using GMSH if available
            if GMSH_AVAILABLE and MESHIO_AVAILABLE:
                try:
                    self._create_gmsh_mesh(vertices, faces)
                    logging.info(f"GMSH mesh created with {self.fem_mesh.num_vertices()} vertices and {self.fem_mesh.num_cells()} cells")
                    return
                except Exception as e:
                    logging.warning(f"GMSH mesh creation failed: {e}, falling back to simple mesh")

            # Fallback: Create simple tetrahedral mesh from surface mesh
            self._create_simple_tetrahedral_mesh(vertices, faces)

        except Exception as e:
            logging.error(f"Error setting up FEM mesh: {e}")
            self._create_fallback_mesh()

    def _create_gmsh_mesh(self, vertices, faces):
        """Create mesh using GMSH for better quality tetrahedral meshes"""
        import tempfile
        import os
        
        # Repair mesh before sending to GMSH
        try:
            mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
            trimesh.repair.fix_normals(mesh)
            trimesh.repair.fix_inversion(mesh)
            # Update vertices and faces from repaired mesh
            vertices = mesh.vertices
            faces = mesh.faces
        except Exception as e:
            logging.warning(f"Mesh repair failed: {e}, using original mesh")

        # Initialize GMSH
        if not gmsh.is_initialized():
            gmsh.initialize()
        
        # Create a new model
        gmsh.model.add("fem_mesh")

        try:
            # Add vertices
            vertex_tags = []
            for i, vertex in enumerate(vertices):
                tag = gmsh.model.geo.addPoint(vertex[0], vertex[1], vertex[2])
                vertex_tags.append(tag)

            # Add surfaces if faces are available
            if len(faces) > 0:
                # Alternative: Create discrete entity directly
                # This is much faster and more robust for existing meshes
                
                # Clear geometry and use addDiscreteEntity
                gmsh.model.remove()
                gmsh.model.add("fem_mesh")
                
                # Add discrete surface
                surf_tag = gmsh.model.addDiscreteEntity(2)
                
                # Flatten faces for setCoordinates
                flat_coords = vertices.flatten()
                flat_faces = faces.flatten() + 1  # 1-based indexing for GMSH
                
                # Set mesh nodes
                num_nodes = len(vertices)
                node_tags = np.arange(1, num_nodes + 1)
                gmsh.model.mesh.addNodes(2, surf_tag, node_tags, flat_coords)
                
                # Set mesh elements (triangles)
                # type 2 is 3-node triangle
                gmsh.model.mesh.addElements(2, surf_tag, [2], [np.arange(1, len(faces) + 1)], [flat_faces])
                
                # Create a volume from the discrete surface
                # First create a surface loop
                sl = gmsh.model.geo.addSurfaceLoop([surf_tag])
                vol = gmsh.model.geo.addVolume([sl])
                
                gmsh.model.geo.synchronize()
                
                # Calculate bounding box
                min_coords = np.min(vertices, axis=0)
                max_coords = np.max(vertices, axis=0)
                bbox_diag = np.linalg.norm(max_coords - min_coords)
                
                # Set mesh size options
                min_mesh_size = bbox_diag * 0.02
                max_mesh_size = bbox_diag * 0.10
                
                gmsh.option.setNumber("Mesh.MeshSizeMin", min_mesh_size)
                gmsh.option.setNumber("Mesh.MeshSizeMax", max_mesh_size)
                
                # Try generation with different algorithms
                algorithms = [
                    (1, "MeshAdapt"),
                    (4, "Frontal"),
                    (5, "Delaunay"),
                    (6, "Frontal for 2D"),
                    (10, "HXT")
                ]
                
                success = False
                last_error = None
                
                for algo_id, algo_name in algorithms:
                    try:
                        logging.info(f"Trying GMSH 3D generation with {algo_name}...")
                        gmsh.option.setNumber("Mesh.Algorithm3D", algo_id)
                        
                        # Set tolerance to be more forgiving
                        gmsh.option.setNumber("Geometry.Tolerance", 1e-4)
                        
                        gmsh.model.mesh.generate(3)
                        success = True
                        break
                    except Exception as e:
                        logging.warning(f"GMSH {algo_name} failed: {e}")
                        last_error = e
                
                if not success:
                    raise ValueError(f"All GMSH algorithms failed. Last error: {last_error}")

            # Save to temporary file
            with tempfile.NamedTemporaryFile(suffix='.msh', delete=False) as tmp_file:
                tmp_filename = tmp_file.name

            gmsh.write(tmp_filename)
            
            # Convert to XDMF using meshio
            m = meshio.read(tmp_filename)

            # Extract tetrahedral cells
            tetra_cells = []
            if "tetra" in m.cell_data_dict.get("gmsh:physical", {}):
                tetra_cells = [("tetra", m.get_cells_type("tetra"))]
            elif "tetra" in m.cells_dict:
                 tetra_cells = [("tetra", m.cells_dict["tetra"])]

            if not tetra_cells:
                # If no tetra cells found, try to see if we have any 3D cells
                logging.warning("No tetrahedral cells found in GMSH output")
                raise ValueError("No tetrahedral cells generated")

            # Save as XDMF
            xdmf_filename = tmp_filename.replace('.msh', '.xdmf')
            
            meshio.write_points_cells(
                xdmf_filename,
                m.points,
                tetra_cells
            )

            # Load in FEniCS
            self.fem_mesh = Mesh()
            with XDMFFile(xdmf_filename) as f:
                f.read(self.fem_mesh)

            # Create function space
            self.function_space = FunctionSpace(self.fem_mesh, 'P', 1)
            self.temperature_field = Function(self.function_space)
            self.temperature_field.vector()[:] = self.ambient_temp

            # Clean up temporary files
            try:
                os.unlink(tmp_filename)
                os.unlink(xdmf_filename)
            except:
                pass
                
        except Exception as e:
            logging.error(f"GMSH generation process failed: {e}")
            if 'tmp_filename' in locals():
                try:
                    os.unlink(tmp_filename)
                except:
                    pass
            raise e
        finally:
            # Don't finalize if we want to keep GMSH alive, but usually good practice to clear
            gmsh.model.remove()
            # gmsh.finalize() # Don't finalize as we might need it again
    
    def _create_simple_tetrahedral_mesh(self, vertices, faces):
        """Create simple tetrahedral mesh using Delaunay triangulation"""
        if not FENICS_AVAILABLE:
            return

        try:
            from scipy.spatial import Delaunay

            # Use Delaunay triangulation to create tetrahedra
            if len(vertices) >= 4:
                tri = Delaunay(vertices)
                tetrahedra = tri.simplices

                # Create FEniCS mesh
                self.fem_mesh = Mesh()
                editor = MeshEditor()
                editor.open(self.fem_mesh, "tetrahedron", 3, 3)

                # Add vertices
                editor.init_vertices(len(vertices))
                for i, coord in enumerate(vertices):
                    editor.add_vertex(i, coord)

                # Add cells
                editor.init_cells(len(tetrahedra))
                for i, tet in enumerate(tetrahedra):
                    editor.add_cell(i, tet)

                editor.close()

                # Create function space
                self.function_space = FunctionSpace(self.fem_mesh, 'P', 1)
                self.temperature_field = Function(self.function_space)
                self.temperature_field.vector()[:] = self.ambient_temp

                logging.info(f"Simple tetrahedral mesh created with {self.fem_mesh.num_vertices()} vertices and {self.fem_mesh.num_cells()} cells")
            else:
                logging.warning("Not enough vertices for tetrahedral mesh, using fallback")
                self._create_fallback_mesh()

        except Exception as e:
            logging.error(f"Error creating simple tetrahedral mesh: {e}")
            self._create_fallback_mesh()

    def _create_fallback_mesh(self):
        """Create a very simple fallback mesh"""
        if not FENICS_AVAILABLE:
            return

        try:
            # Create a simple unit cube mesh
            mesh = UnitCubeMesh(8, 8, 8)
            self.fem_mesh = mesh
            self.function_space = FunctionSpace(self.fem_mesh, 'P', 1)
            self.temperature_field = Function(self.function_space)
            self.temperature_field.vector()[:] = self.ambient_temp

            logging.info("Created fallback unit cube mesh for FEM simulation")

        except Exception as e:
            logging.error(f"Error creating fallback mesh: {e}")
    
    def solve_heat_equation(self, dt: float) -> np.ndarray:
        """
        Solve the heat equation using FEM
        
        Args:
            dt: Time step
            
        Returns:
            Temperature field as numpy array
        """
        if not FENICS_AVAILABLE or self.fem_mesh is None:
            return self._fallback_heat_simulation(dt)
        
        try:
            # Define trial and test functions
            u = TrialFunction(self.function_space)
            v = TestFunction(self.function_space)
            
            # Previous temperature
            u_n = self.temperature_field
            
            # Material properties
            thermal_conductivity = self.material_props.get('thermal_conductivity', 167)  # W/(m·K)
            density = self.material_props.get('density', 2700)  # kg/m³
            specific_heat = self.material_props.get('specific_heat', 900)  # J/(kg·K)
            
            # Thermal diffusivity
            alpha = thermal_conductivity / (density * specific_heat)
            
            # Heat source term
            heat_source = self._define_heat_source()
            
            # Variational formulation
            # ∂u/∂t = α∇²u + Q/(ρcp)
            F = (u - u_n) / dt * v * dx + alpha * dot(grad(u), grad(v)) * dx - heat_source * v * dx
            
            # Separate into bilinear and linear forms
            a = lhs(F)
            L = rhs(F)
            
            # Apply boundary conditions
            bcs = self._apply_boundary_conditions()
            
            # Solve
            u_new = Function(self.function_space)
            solve(a == L, u_new, bcs)
            
            # Update temperature field
            self.temperature_field.assign(u_new)
            
            # Convert to numpy array for visualization
            temp_array = self.temperature_field.vector().get_local()
            
            # Store solution history
            self.solution_history.append({
                'time': self.current_time,
                'temperature': temp_array.copy(),
                'max_temp': np.max(temp_array),
                'min_temp': np.min(temp_array),
                'avg_temp': np.mean(temp_array)
            })
            
            return temp_array
            
        except Exception as e:
            logging.error(f"Error solving heat equation: {e}")
            return self._fallback_heat_simulation(dt)
    
    def _define_heat_source(self):
        """Define heat source term for the FEM formulation"""
        if not FENICS_AVAILABLE:
            return None
            
        try:
            # Heat source location (center of domain)
            mesh_coords = self.fem_mesh.coordinates()
            center = np.mean(mesh_coords, axis=0)
            
            # Gaussian heat source
            if USER_DEFINED_EXPR_AVAILABLE:
                # Use UserDefinedExpression for older FEniCS
                class HeatSource(UserDefinedExpression):
                    def __init__(self, center, radius, power, **kwargs):
                        super().__init__(**kwargs)
                        self.center = center
                        self.radius = radius
                        self.power = power
                    
                    def eval(self, values, x):
                        r_squared = sum((x[i] - self.center[i])**2 for i in range(len(x)))
                        values[0] = self.power * exp(-r_squared / (2 * self.radius**2))
                    
                    def value_shape(self):
                        return ()
                
                return HeatSource(center, self.heat_source_radius, self.heat_source_power, degree=2)
            else:
                # Use Expression for newer FEniCS versions
                try:
                    # Try Expression class
                    heat_source_expr = f"{self.heat_source_power} * exp(-(pow(x[0]-{center[0]}, 2) + pow(x[1]-{center[1]}, 2) + pow(x[2]-{center[2]}, 2)) / (2 * {self.heat_source_radius**2}))"
                    return Expression(heat_source_expr, degree=2)
                except:
                    # Fallback to constant if Expression fails
                    print("Warning: Could not create heat source expression, using constant")
                    return Constant(0.0)
            
        except Exception as e:
            logging.error(f"Error defining heat source: {e}")
            return Constant(0.0)
    
    def _apply_boundary_conditions(self):
        """Apply boundary conditions for heat transfer"""
        if not FENICS_AVAILABLE:
            return []
            
        try:
            bcs = []
            
            # Convective boundary condition on all external surfaces
            # This is simplified - in reality, you'd mark different boundaries
            
            # For now, apply ambient temperature on boundaries
            def boundary(x, on_boundary):
                return on_boundary
            
            bc = DirichletBC(self.function_space, Constant(self.ambient_temp), boundary)
            bcs.append(bc)
            
            return bcs
            
        except Exception as e:
            logging.error(f"Error applying boundary conditions: {e}")
            return []
    
    def _fallback_heat_simulation(self, dt: float) -> np.ndarray:
        """Fallback simulation when FEniCS is not available"""
        vertices = np.array(self.mesh_data.get('vertices', []))
        
        if len(vertices) == 0:
            return np.array([self.ambient_temp])
        
        # Initialize temperature field if needed
        if not hasattr(self, '_temp_field') or self._temp_field is None:
            self._temp_field = np.full(len(vertices), self.ambient_temp, dtype=np.float32)
        
        # Simple heat diffusion
        thermal_conductivity = self.material_props.get('thermal_conductivity', 167)
        density = self.material_props.get('density', 2700)
        specific_heat = self.material_props.get('specific_heat', 900)
        
        alpha = thermal_conductivity / (density * specific_heat)
        
        # Heat source effect
        center = np.mean(vertices, axis=0)
        distances = np.linalg.norm(vertices - center, axis=1)
        heat_influence = np.exp(-(distances ** 2) / (2 * self.heat_source_radius ** 2))
        
        # Apply heat source
        self._temp_field += heat_influence * (self.heat_source_temp - self._temp_field) * dt * 0.1
        
        # Simple diffusion
        for i in range(len(vertices)):
            neighbors = np.where(distances < 0.02)[0]  # 2cm radius
            if len(neighbors) > 1:
                avg_neighbor_temp = np.mean(self._temp_field[neighbors])
                self._temp_field[i] += alpha * (avg_neighbor_temp - self._temp_field[i]) * dt * 100
        
        # Cooling
        self._temp_field += (self.ambient_temp - self._temp_field) * 0.01 * dt
        
        return self._temp_field
    
    def step(self, dt: float) -> Dict:
        """
        Advance simulation by one time step
        
        Args:
            dt: Time step in seconds
            
        Returns:
            Dictionary with simulation results
        """
        self.current_time += dt
        
        # Solve heat equation
        temp_field = self.solve_heat_equation(dt)
        
        # Calculate statistics
        max_temp = float(np.max(temp_field))
        min_temp = float(np.min(temp_field))
        avg_temp = float(np.mean(temp_field))
        
        # Progress calculation
        progress = min(100.0, (self.current_time / self.total_time) * 100)
        
        return {
            'time': self.current_time,
            'progress': progress,
            'temperature_field': temp_field.tolist(),
            'max_temp': max_temp,
            'min_temp': min_temp,
            'avg_temp': avg_temp,
            'simulation_complete': self.current_time >= self.total_time
        }
    
    def get_mesh_for_visualization(self) -> Optional[Dict]:
        """
        Get mesh data optimized for 3D visualization
        
        Returns:
            Dictionary with vertices, faces, and temperature data
        """
        if not FENICS_AVAILABLE or self.fem_mesh is None:
            return {
                'vertices': self.mesh_data.get('vertices', []),
                'faces': self.mesh_data.get('faces', []),
                'temperature_field': getattr(self, '_temp_field', []).tolist() if hasattr(self, '_temp_field') else []
            }
        
        try:
            # Extract mesh data from FEniCS
            vertices = self.fem_mesh.coordinates()
            cells = self.fem_mesh.cells()
            
            # Get temperature values at vertices
            temp_values = self.temperature_field.vector().get_local()
            
            return {
                'vertices': vertices.tolist(),
                'faces': cells.tolist(),
                'temperature_field': temp_values.tolist(),
                'mesh_type': 'fem_mesh'
            }
            
        except Exception as e:
            logging.error(f"Error extracting mesh for visualization: {e}")
            return None
    
    def export_results(self, filename: str):
        """Export simulation results for analysis"""
        if PYVISTA_AVAILABLE and self.solution_history:
            try:
                # Create PyVista mesh for export
                vertices = np.array(self.mesh_data.get('vertices', []))
                faces = np.array(self.mesh_data.get('faces', []))
                
                if len(vertices) > 0 and len(faces) > 0:
                    # Create PyVista mesh
                    mesh = pv.PolyData(vertices, faces)
                    
                    # Add temperature data from final solution
                    final_temp = self.solution_history[-1]['temperature']
                    mesh.point_data['Temperature'] = final_temp
                    
                    # Save as VTK file
                    mesh.save(f"{filename}.vtk")
                    logging.info(f"Results exported to {filename}.vtk")
                    
            except Exception as e:
                logging.error(f"Error exporting results: {e}")
    
    def reset(self):
        """Reset simulation to initial state"""
        self.current_time = 0.0
        self.solution_history = []
        
        if FENICS_AVAILABLE and self.temperature_field is not None:
            self.temperature_field.vector()[:] = self.ambient_temp
        
        if hasattr(self, '_temp_field'):
            self._temp_field.fill(self.ambient_temp)
