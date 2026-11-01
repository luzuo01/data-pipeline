# Franka Panda UE5 asset

The link meshes in `Source/` originate from the official `frankarobotics/franka_ros`
repository, revision `ddd2fffd9de44b02ad15b4bbb2bfa2cec4d60d98`:

<https://github.com/frankarobotics/franka_ros/tree/ddd2fffd9de44b02ad15b4bbb2bfa2cec4d60d98/franka_description>

The source package is Apache-2.0 licensed. The original visual files are DAE;
they were converted to one FBX per Panda link with Blender 4.3.2, then imported
into UE5.6 as static meshes. `link0` through `link7`, `hand`, and `finger` are
the stable UE asset names consumed by `AFrankaPandaRobot`.

The robot actor is a deterministic kinematic rendering/control bridge. It
implements the seven Panda revolute joints and gripper opening for scene
preview and Remote Control. It does not claim rigid-body dynamics, self
collision certification, or a motion planner; those remain separate from this
UE SceneGenerator integration.

The upstream Apache-2.0 license is included in LICENSE. Converted meshes use
UE centimetres with ROS Y reflected. Scale 1 is life size. The actor includes
the original joint limits and visual/hand/TCP offsets; it does not use the
URDF inertial parameters or collision capsules as a dynamics model.
