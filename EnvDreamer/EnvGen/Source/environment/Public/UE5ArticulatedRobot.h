#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "UE5ArticulatedRobot.generated.h"

class UStaticMeshComponent;

/**
 * Generic UE5 kinematic bridge for the robot assets shipped with EnvGen.
 *
 * Sawyer and UR5e are imported as link meshes rather than skeletal assets.
 * This actor gives those meshes an explicit joint chain, limits, TCP query,
 * and gripper command so the UE5 backend can replay a measured trajectory.
 * Rigid tabletop objects remain regular UE physics actors; this actor is the
 * commanded articulation used by the task runtime and recorder.
 */
UCLASS(Blueprintable, BlueprintType)
class ENVIRONMENT_API AUE5ArticulatedRobot : public AActor
{
    GENERATED_BODY()

public:
    AUE5ArticulatedRobot();

    virtual void OnConstruction(const FTransform& Transform) override;

    UFUNCTION(BlueprintCallable, Category="UE5 Robot|Control")
    bool SetRobotModel(const FString& Model);

    UFUNCTION(BlueprintCallable, Category="UE5 Robot|Control")
    bool SetJointPositions(const TArray<float>& Values);

    UFUNCTION(BlueprintCallable, Category="UE5 Robot|Control")
    bool SetJointPositions7(float J1, float J2, float J3, float J4,
                            float J5, float J6, float J7);

    UFUNCTION(BlueprintCallable, Category="UE5 Robot|Control")
    bool SetGripper(float OpeningMeters);

    UFUNCTION(BlueprintPure, Category="UE5 Robot|Control")
    TArray<float> GetJointPositions() const { return JointPositions; }

    UFUNCTION(BlueprintPure, Category="UE5 Robot|Control")
    int32 GetDOF() const { return DOF; }

    UFUNCTION(BlueprintPure, Category="UE5 Robot|Control")
    FVector GetToolCenterPoint() const;

    UFUNCTION(BlueprintCallable, Category="UE5 Robot|Control")
    void UpdateKinematics();

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="UE5 Robot|Control")
    FString RobotModel = TEXT("Sawyer");

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="UE5 Robot|Assets")
    FString MeshRoot;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="UE5 Robot|Control")
    TArray<float> JointPositions;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="UE5 Robot|Control")
    float GripperOpeningMeters = 0.04f;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category="UE5 Robot|Control")
    int32 DOF = 7;

protected:
    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category="UE5 Robot|Components")
    USceneComponent* RobotRoot;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category="UE5 Robot|Components")
    TArray<UStaticMeshComponent*> LinkMeshes;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category="UE5 Robot|Components")
    UStaticMeshComponent* ToolMesh;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category="UE5 Robot|Components")
    UStaticMeshComponent* FingerLeftMesh;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category="UE5 Robot|Components")
    UStaticMeshComponent* FingerRightMesh;

    void ConfigureModel();
    void LoadMeshes();
    void ConfigureMesh(UStaticMeshComponent* Component, const FString& AssetName);
};
