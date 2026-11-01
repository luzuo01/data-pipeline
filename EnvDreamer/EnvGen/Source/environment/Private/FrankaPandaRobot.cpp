#include "FrankaPandaRobot.h"

#include "Components/SceneComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "UObject/SoftObjectPath.h"

namespace
{
    constexpr float M = 100.0f;

    // Origins and fixed frame rotations copied from franka_description's
    // franka_arm.xacro (metres, then converted to UE centimetres).
    const FVector JointOrigins[7] = {
        FVector(0.0f, 0.0f, 0.333f), FVector(0.0f, 0.0f, 0.0f),
        FVector(0.0f, -0.316f, 0.0f), FVector(0.0825f, 0.0f, 0.0f),
        FVector(-0.0825f, 0.384f, 0.0f), FVector(0.0f, 0.0f, 0.0f),
        FVector(0.088f, 0.0f, 0.0f)
    };

    const float JointRolls[7] = {0, -HALF_PI, HALF_PI, HALF_PI, -HALF_PI, HALF_PI, HALF_PI};
    const float Lower[7] = {-2.8973f, -1.7628f, -2.8973f, -3.0718f, -2.8973f, -0.0175f, -2.8973f};
    const float Upper[7] = {2.8973f, 1.7628f, 2.8973f, -0.0698f, 2.8973f, 3.7525f, 2.8973f};
}

AFrankaPandaRobot::AFrankaPandaRobot()
{
    PrimaryActorTick.bCanEverTick = false;
    RobotRoot = CreateDefaultSubobject<USceneComponent>(TEXT("RobotRoot"));
    SetRootComponent(RobotRoot);

    for (int32 Index = 0; Index < 8; ++Index)
    {
        UStaticMeshComponent* Link = CreateDefaultSubobject<UStaticMeshComponent>(
            *FString::Printf(TEXT("Link%d"), Index));
        Link->SetupAttachment(RobotRoot);
        Link->SetMobility(EComponentMobility::Movable);
        Link->SetCollisionEnabled(ECollisionEnabled::QueryAndPhysics);
        Link->SetCollisionProfileName(TEXT("BlockAllDynamic"));
        LinkMeshes.Add(Link);
    }
    HandMesh = CreateDefaultSubobject<UStaticMeshComponent>(TEXT("Hand"));
    FingerLeftMesh = CreateDefaultSubobject<UStaticMeshComponent>(TEXT("FingerLeft"));
    FingerRightMesh = CreateDefaultSubobject<UStaticMeshComponent>(TEXT("FingerRight"));
    for (UStaticMeshComponent* Component : {HandMesh, FingerLeftMesh, FingerRightMesh})
    {
        Component->SetupAttachment(RobotRoot);
        Component->SetMobility(EComponentMobility::Movable);
        Component->SetCollisionEnabled(ECollisionEnabled::QueryAndPhysics);
        Component->SetCollisionProfileName(TEXT("BlockAllDynamic"));
    }
    JointPositions = {0.0f, -PI / 4, 0.0f, -3 * PI / 4, 0.0f, PI / 2, PI / 4};
}

void AFrankaPandaRobot::OnConstruction(const FTransform& Transform)
{
    Super::OnConstruction(Transform);
    LoadMeshes();
    UpdateKinematics();
}

void AFrankaPandaRobot::ConfigureMesh(UStaticMeshComponent* Component, const FString& AssetName)
{
    if (!Component) return;
    const FString ObjectPath = MeshRoot / AssetName + TEXT(".") + AssetName;
    if (UStaticMesh* Mesh = Cast<UStaticMesh>(StaticLoadObject(UStaticMesh::StaticClass(), nullptr, *ObjectPath)))
    {
        Component->SetStaticMesh(Mesh);
        Component->SetVisibility(true);
    }
    else
    {
        Component->SetVisibility(false);
        UE_LOG(LogTemp, Warning, TEXT("FrankaPandaRobot: mesh not imported yet: %s"), *ObjectPath);
    }
}

void AFrankaPandaRobot::LoadMeshes()
{
    for (int32 Index = 0; Index < LinkMeshes.Num(); ++Index)
    {
        ConfigureMesh(LinkMeshes[Index], FString::Printf(TEXT("link%d"), Index));
    }
    ConfigureMesh(HandMesh, TEXT("hand"));
    ConfigureMesh(FingerLeftMesh, TEXT("finger"));
    ConfigureMesh(FingerRightMesh, TEXT("finger"));
}

bool AFrankaPandaRobot::SetJointPositions(const TArray<float>& Values)
{
    if (Values.Num() != 7) return false;
    for (int32 Index = 0; Index < 7; ++Index)
    {
        if (!FMath::IsFinite(Values[Index]) || Values[Index] < Lower[Index] || Values[Index] > Upper[Index]) return false;
    }
    JointPositions = Values;
    UpdateKinematics();
    return true;
}

bool AFrankaPandaRobot::SetJointPositions7(float J1, float J2, float J3, float J4,
                                           float J5, float J6, float J7)
{
    TArray<float> Values = {J1, J2, J3, J4, J5, J6, J7};
    return SetJointPositions(Values);
}

bool AFrankaPandaRobot::SetGripper(float OpeningMeters)
{
    if (!FMath::IsFinite(OpeningMeters)) return false;
    GripperOpeningMeters = FMath::Clamp(OpeningMeters, 0.0f, 0.08f);
    UpdateKinematics();
    return true;
}

void AFrankaPandaRobot::UpdateKinematics()
{
    if (JointPositions.Num() != 7 || LinkMeshes.Num() != 8) return;
    LinkMeshes[0]->SetRelativeTransform(FTransform::Identity);
    FVector Position = FVector::ZeroVector;
    FQuat Orientation = FQuat::Identity;
    for (int32 Index = 0; Index < 7; ++Index)
    {
        // ROS metres -> UE centimetres: S=diag(1,-1,1).  Rotations
        // transform as S R S, so ROS rotations around X/Z reverse sign.
        const FVector RosOrigin = JointOrigins[Index];
        Position += Orientation.RotateVector(FVector(RosOrigin.X, -RosOrigin.Y, RosOrigin.Z) * M);
        Orientation = Orientation * FQuat(FVector::ForwardVector, -JointRolls[Index])
            * FQuat(FVector::UpVector, -JointPositions[Index]);
        Orientation.Normalize();
        // link7's visual origin has a +pi/4 yaw in the official xacro.
        const FQuat VisualRotation = Index == 6
            ? Orientation * FQuat(FVector::UpVector, -PI / 4) : Orientation;
        LinkMeshes[Index + 1]->SetRelativeTransform(FTransform(VisualRotation, Position));
    }
    const FVector HandPosition = Position + Orientation.RotateVector(FVector(0, 0, 10.7f));
    const FQuat HandRotation = Orientation * FQuat(FVector::UpVector, PI / 4);
    HandMesh->SetRelativeTransform(FTransform(HandRotation, HandPosition));
    const float HalfGap = GripperOpeningMeters * M * 0.5f;
    FingerLeftMesh->SetRelativeTransform(FTransform(HandRotation,
        HandPosition + HandRotation.RotateVector(FVector(0, -HalfGap, 5.84f))));
    FingerRightMesh->SetRelativeTransform(FTransform(HandRotation * FQuat(FVector::UpVector, PI),
        HandPosition + HandRotation.RotateVector(FVector(0, HalfGap, 5.84f))));
}

FVector AFrankaPandaRobot::GetToolCenterPoint() const
{
    // Official panda_hand_tcp frame, 0.1034 m along hand Z.
    return HandMesh ? HandMesh->GetComponentTransform().TransformPosition(FVector(0, 0, 10.34f)) : GetActorLocation();
}
