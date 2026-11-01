#include "UE5ArticulatedRobot.h"

#include "Components/SceneComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/StaticMesh.h"

namespace
{
    constexpr float M = 100.0f;

    static bool IsUR5e(const FString& Model)
    {
        return Model.Equals(TEXT("UR5e"), ESearchCase::IgnoreCase) ||
               Model.Equals(TEXT("UR5"), ESearchCase::IgnoreCase);
    }

    static FTransform JointTransform(float A, float D, float Alpha, float Theta)
    {
        const float C = FMath::Cos(Theta), S = FMath::Sin(Theta);
        const float CA = FMath::Cos(Alpha), SA = FMath::Sin(Alpha);
        return FTransform(
            FMatrix(FPlane(C, S, 0, 0), FPlane(-S * CA, C * CA, SA, 0),
                    FPlane(S * SA, -C * SA, CA, 0), FPlane(A * M, 0, D * M, 1)));
    }
}

AUE5ArticulatedRobot::AUE5ArticulatedRobot()
{
    PrimaryActorTick.bCanEverTick = false;
    RobotRoot = CreateDefaultSubobject<USceneComponent>(TEXT("RobotRoot"));
    SetRootComponent(RobotRoot);
    for (int32 Index = 0; Index < 7; ++Index)
    {
        UStaticMeshComponent* Link = CreateDefaultSubobject<UStaticMeshComponent>(
            *FString::Printf(TEXT("Link%d"), Index));
        Link->SetupAttachment(RobotRoot);
        Link->SetMobility(EComponentMobility::Movable);
        Link->SetCollisionEnabled(ECollisionEnabled::QueryAndPhysics);
        Link->SetCollisionProfileName(TEXT("BlockAllDynamic"));
        LinkMeshes.Add(Link);
    }
    ToolMesh = CreateDefaultSubobject<UStaticMeshComponent>(TEXT("Tool"));
    FingerLeftMesh = CreateDefaultSubobject<UStaticMeshComponent>(TEXT("FingerLeft"));
    FingerRightMesh = CreateDefaultSubobject<UStaticMeshComponent>(TEXT("FingerRight"));
    for (UStaticMeshComponent* Component : {ToolMesh, FingerLeftMesh, FingerRightMesh})
    {
        Component->SetupAttachment(RobotRoot);
        Component->SetMobility(EComponentMobility::Movable);
        Component->SetCollisionEnabled(ECollisionEnabled::QueryAndPhysics);
        Component->SetCollisionProfileName(TEXT("BlockAllDynamic"));
    }
    ConfigureModel();
}

void AUE5ArticulatedRobot::OnConstruction(const FTransform& Transform)
{
    Super::OnConstruction(Transform);
    ConfigureModel();
    LoadMeshes();
    UpdateKinematics();
}

void AUE5ArticulatedRobot::ConfigureModel()
{
    const bool bUR5e = IsUR5e(RobotModel);
    DOF = bUR5e ? 6 : 7;
    MeshRoot = bUR5e ? TEXT("/Game/Robots/PORTALTrace/UR5e/")
                     : TEXT("/Game/Robots/PORTALTrace/Sawyer/");
    if (JointPositions.Num() != DOF)
    {
        JointPositions.Init(0.0f, DOF);
        if (!bUR5e)
        {
            JointPositions = {0.0f, -0.5f, 0.0f, 1.2f, 0.0f, 0.8f, 0.0f};
        }
        else
        {
            JointPositions = {0.0f, -1.1f, 1.2f, -1.6f, -1.57f, 0.0f};
        }
    }
}

void AUE5ArticulatedRobot::ConfigureMesh(UStaticMeshComponent* Component, const FString& AssetName)
{
    if (!Component) return;
    const FString ObjectPath = AssetName.StartsWith(TEXT("/"))
        ? AssetName
        : MeshRoot + AssetName + TEXT(".") + AssetName;
    if (UStaticMesh* Mesh = Cast<UStaticMesh>(StaticLoadObject(UStaticMesh::StaticClass(), nullptr, *ObjectPath)))
    {
        Component->SetStaticMesh(Mesh);
        Component->SetVisibility(true);
    }
    else
    {
        Component->SetVisibility(false);
        UE_LOG(LogTemp, Warning, TEXT("UE5ArticulatedRobot: missing mesh %s"), *ObjectPath);
    }
}

void AUE5ArticulatedRobot::LoadMeshes()
{
    const bool bUR5e = IsUR5e(RobotModel);
    if (bUR5e)
    {
        const TCHAR* Names[] = {TEXT("base_vis_0"), TEXT("shoulder_vis_0"), TEXT("upperarm_vis_0"),
                                TEXT("forearm_vis_0"), TEXT("wrist1_vis_0"), TEXT("wrist2_vis_0"), TEXT("wrist3_vis")};
        for (int32 Index = 0; Index < LinkMeshes.Num(); ++Index) ConfigureMesh(LinkMeshes[Index], Names[Index]);
        ConfigureMesh(ToolMesh, TEXT("wrist3_vis"));
    }
    else
    {
        const TCHAR* Names[] = {TEXT("l0_0"), TEXT("l1_0"), TEXT("l2_0"), TEXT("l3_0"),
                                TEXT("l4_0"), TEXT("l5_0"), TEXT("l6_0")};
        for (int32 Index = 0; Index < LinkMeshes.Num(); ++Index) ConfigureMesh(LinkMeshes[Index], Names[Index]);
        ConfigureMesh(ToolMesh, TEXT("head_0"));
    }
    // Generic fingers are intentionally simple and deterministic.  They are
    // collision-enabled UE meshes and are sufficient for stack replay.
    ConfigureMesh(FingerLeftMesh, TEXT("/Engine/BasicShapes/Cube.Cube"));
    ConfigureMesh(FingerRightMesh, TEXT("/Engine/BasicShapes/Cube.Cube"));
}

bool AUE5ArticulatedRobot::SetRobotModel(const FString& Model)
{
    if (!Model.Equals(TEXT("Sawyer"), ESearchCase::IgnoreCase) &&
        !Model.Equals(TEXT("UR5e"), ESearchCase::IgnoreCase) &&
        !Model.Equals(TEXT("UR5"), ESearchCase::IgnoreCase)) return false;
    RobotModel = Model.Equals(TEXT("UR5"), ESearchCase::IgnoreCase) ? TEXT("UR5e") : Model;
    ConfigureModel();
    LoadMeshes();
    UpdateKinematics();
    return true;
}

bool AUE5ArticulatedRobot::SetJointPositions(const TArray<float>& Values)
{
    if (Values.Num() != DOF) return false;
    for (float Value : Values) if (!FMath::IsFinite(Value) || FMath::Abs(Value) > 6.4f) return false;
    JointPositions = Values;
    UpdateKinematics();
    return true;
}

bool AUE5ArticulatedRobot::SetJointPositions7(float J1, float J2, float J3, float J4,
                                               float J5, float J6, float J7)
{
    TArray<float> Values = {J1, J2, J3, J4, J5, J6};
    if (DOF == 7) Values.Add(J7);
    return SetJointPositions(Values);
}

bool AUE5ArticulatedRobot::SetGripper(float OpeningMeters)
{
    if (!FMath::IsFinite(OpeningMeters)) return false;
    GripperOpeningMeters = FMath::Clamp(OpeningMeters, 0.0f, 0.12f);
    UpdateKinematics();
    return true;
}

void AUE5ArticulatedRobot::UpdateKinematics()
{
    if (JointPositions.Num() != DOF) return;
    const bool bUR5e = IsUR5e(RobotModel);
    FTransform Current = FTransform::Identity;
    if (bUR5e)
    {
        const float A[] = {0.0f, -0.425f, -0.3922f, 0.0f, 0.0f, 0.0f};
        const float D[] = {0.1625f, 0.0f, 0.0f, 0.1333f, 0.0997f, 0.0996f};
        const float Alpha[] = {HALF_PI, 0.0f, 0.0f, HALF_PI, -HALF_PI, 0.0f};
        for (int32 Index = 0; Index < 6; ++Index)
        {
            Current = Current * JointTransform(A[Index], D[Index], Alpha[Index], JointPositions[Index]);
            LinkMeshes[Index]->SetRelativeTransform(Current);
        }
        LinkMeshes[6]->SetRelativeTransform(Current);
    }
    else
    {
        const FVector Origins[] = {FVector(0,0,0.317f), FVector(0,0,0), FVector(0,-0.1925f,0),
                                   FVector(0.081f,0,0.4f), FVector(0,0,0), FVector(0,0,0.168f), FVector(0,0,0.1f)};
        const float Rolls[] = {0, -HALF_PI, HALF_PI, HALF_PI, -HALF_PI, HALF_PI, HALF_PI};
        for (int32 Index = 0; Index < 7; ++Index)
        {
            Current.SetLocation(Current.GetLocation() + Current.GetRotation().RotateVector(FVector(Origins[Index].X, -Origins[Index].Y, Origins[Index].Z) * M));
            Current.SetRotation(Current.GetRotation() * FQuat(FVector::ForwardVector, -Rolls[Index]) * FQuat(FVector::UpVector, -JointPositions[Index]));
            Current.NormalizeRotation();
            LinkMeshes[Index]->SetRelativeTransform(Current);
        }
    }
    const FVector ToolPosition = Current.GetLocation() + Current.GetRotation().RotateVector(FVector(0, 0, 10.0f));
    ToolMesh->SetRelativeTransform(FTransform(Current.GetRotation(), ToolPosition));
    const float HalfGap = GripperOpeningMeters * M * 0.5f;
    FingerLeftMesh->SetRelativeTransform(FTransform(Current.GetRotation(), ToolPosition + Current.GetRotation().RotateVector(FVector(0, -HalfGap, 4.0f))));
    FingerRightMesh->SetRelativeTransform(FTransform(Current.GetRotation(), ToolPosition + Current.GetRotation().RotateVector(FVector(0, HalfGap, 4.0f))));
    FingerLeftMesh->SetRelativeScale3D(FVector(0.02f, 0.01f, 0.04f));
    FingerRightMesh->SetRelativeScale3D(FVector(0.02f, 0.01f, 0.04f));
}

FVector AUE5ArticulatedRobot::GetToolCenterPoint() const
{
    return ToolMesh ? ToolMesh->GetComponentLocation() : GetActorLocation();
}
