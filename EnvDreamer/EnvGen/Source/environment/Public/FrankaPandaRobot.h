#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "FrankaPandaRobot.generated.h"

class UStaticMeshComponent;

/**
 * Lightweight UE5 Panda arm for the EnvDreamer scene path.
 *
 * The meshes are the official franka_description visual links.  This actor
 * deliberately keeps the kinematic chain explicit and deterministic so the
 * existing Remote Control scene builder can place and drive it without
 * requiring a third-party UE plugin.  It is a rendering/control bridge; full
 * rigid-body dynamics and collision-free planning remain separate work.
 */
UCLASS(Blueprintable, BlueprintType)
class ENVIRONMENT_API AFrankaPandaRobot : public AActor
{
    GENERATED_BODY()

public:
    AFrankaPandaRobot();

    virtual void OnConstruction(const FTransform& Transform) override;

    UFUNCTION(BlueprintCallable, Category="Franka Panda|Control")
    bool SetJointPositions(const TArray<float>& JointPositions);

    UFUNCTION(BlueprintCallable, Category="Franka Panda|Control")
    bool SetJointPositions7(float J1, float J2, float J3, float J4,
                            float J5, float J6, float J7);

    UFUNCTION(BlueprintCallable, Category="Franka Panda|Control")
    bool SetGripper(float OpeningMeters);

    UFUNCTION(BlueprintPure, Category="Franka Panda|Control")
    TArray<float> GetJointPositions() const { return JointPositions; }

    UFUNCTION(BlueprintPure, Category="Franka Panda|Control")
    FVector GetToolCenterPoint() const;

    /** Recompute all link transforms from the seven joint angles. */
    UFUNCTION(BlueprintCallable, Category="Franka Panda|Control")
    void UpdateKinematics();

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Franka Panda|Assets")
    FString MeshRoot = TEXT("/Game/Robots/FrankaPanda/Source/");

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Franka Panda|Control")
    TArray<float> JointPositions;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Franka Panda|Control")
    float GripperOpeningMeters = 0.04f;

protected:
    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category="Franka Panda|Components")
    USceneComponent* RobotRoot;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category="Franka Panda|Components")
    TArray<UStaticMeshComponent*> LinkMeshes;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category="Franka Panda|Components")
    UStaticMeshComponent* HandMesh;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category="Franka Panda|Components")
    UStaticMeshComponent* FingerLeftMesh;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category="Franka Panda|Components")
    UStaticMeshComponent* FingerRightMesh;

    void LoadMeshes();
    void ConfigureMesh(UStaticMeshComponent* Component, const FString& AssetName);
};
