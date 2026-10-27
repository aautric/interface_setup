nikon_ti.py - control of the Nikon Eclipse Ti-E microscope
==========================================================

The microscope (USB "Nikon USB Microscope", firmware Ti-E V5.50) is driven
through Micro-Manager: its NikonTI device adapter uses the Nikon Ti SDK
installed in C:/Program Files/Nikon. Python talks to Micro-Manager with
pymmcore.

Setup:
  1. Micro-Manager 2.0 installed in C:/Program Files/Micro-Manager-2.0
     (MM_PATH at the top of nikon_ti.py). Its device interface is 70, so
     pymmcore must be 10.1.1.70.x: pymmcore==10.1.1.70.6 (Python <= 3.11,
     numpy < 2), already in the setup_env conda environment.
  2. Close NIS-Elements, Micro-Manager and TiControl before connecting.


The NikonTi class
-----------------
connect() loads the devices the microscope reports. On this Ti:
  TINosePiece  objectives  1-Apo TIRF 100x NA 1.49 Oil, 2-Plan Fluor 60x NA 1.25
                           Oil, 4-Plan Fluor 10x NA 0.30 Dry (3, 5, 6 empty)
  TILightPath  1-Eye100, 2-Left100, 3-Right100, 4-Left80
  TIZDrive     focus (µm)
  TIXYDrive    XY stage (µm)
  TIPFSStatus / TIPFSOffset   Perfect Focus System
The filter cube turret is not motorised (no TIFilterBlock device): the cubes
are listed in FILTER_CUBES (1 original cube, 2 dichroic 3 mm
Di03-R405/488/561/635-t3-25x36) and the one in place is chosen by hand in the
GUI.

  connect() / close()          load / release the microscope (it keeps its state)
  objectives, get_objective(), set_objective(x)
  light_paths, get_light_path(), set_light_path(x)
      x = position as written on the microscope (1, 2...), full label, or a
      part of the label found in only one of them ("60x", "TIRF", "Eye")
  get_z(), set_z(z), move_z(dz)                focus, µm
  get_xy(), set_xy(x, y), move_xy(dx, dy)      stage, µm
  get_pfs_status(), is_pfs_on(), set_pfs(True/False),
  get_pfs_offset(), set_pfs_offset(offset)
  get_metadata()               state of the microscope as a dictionary, to save
                               with the camera images (extra_metadata=...)
  with NikonTi() as ti:        connect() at the start, close() at the end

All moves wait until the device has finished. set_z / set_xy are absolute:
take care of the objective and the sample.

    from nikon_ti import NikonTi
    from camera import Camera

    with NikonTi() as ti, Camera() as cam:
        ti.set_objective("60x")
        ti.set_light_path("Left100")
        image = cam.snap()
        cam.save(image, r"D:\data\2026-10-26", extra_metadata=ti.get_metadata())

Quick check from a terminal (setup_env): python nikon_ti.py
(connects, prints the state, changes nothing)


Status (2026-10-26)
-------------------
  - Connection and reading of all the devices checked on the microscope.
  - Moves (objective, light path, focus, stage, PFS) not run yet.
  - The XY stage reads (0, 0): to check that it is really the motorised stage
    used on the setup.
