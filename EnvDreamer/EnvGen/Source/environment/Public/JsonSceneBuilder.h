#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"

// Asset Registry - Forward declarations
namespace AssetRegistry
{
    static TMap<FString, FString> AssetMap;
    static bool bInitialized = false;

    static void Initialize();
    static FString Resolve(const FString& NameOrPath);
}

#include "Engine/StaticMeshActor.h"
#include "Components/StaticMeshComponent.h"
#include "FrankaPandaRobot.h"
#include "UE5ArticulatedRobot.h"
#include "Engine/StaticMesh.h"
#include "PhysicsEngine/PhysicsConstraintComponent.h"
#include "Materials/Material.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "JsonSceneBuilder.generated.h"

UCLASS(Blueprintable, BlueprintType)
class ENVIRONMENT_API AJsonSceneBuilder : public AActor
{
    GENERATED_BODY()

public:
    AJsonSceneBuilder();

protected:
    virtual void BeginPlay() override;
    virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;

public:
    // ============================================================================
    // SCENE MANAGEMENT
    // ============================================================================

    UFUNCTION(BlueprintCallable, Category = "Scene Builder")
    void ClearAllJsonGeneratedActors();

    UFUNCTION(CallInEditor, BlueprintCallable, Category = "RemoteControl")
    bool RC_Reindex();

    // ============================================================================
    // PRIMARY RC FUNCTIONS (Remote Control API)
    // ============================================================================

    UFUNCTION(CallInEditor, BlueprintCallable, Category = "RemoteControl|Assets")
    bool RC_AddAsset(const FString& Id, const FString& AssetPath, float X, float Y, float Z,
                    float ScaleX, float ScaleY, float ScaleZ,
                    float Pitch, float Yaw, float Roll,
                    const FLinearColor& Color);

    UFUNCTION(CallInEditor, BlueprintCallable, Category = "RemoteControl")
    bool RC_Remove(const FString& Id);

    UFUNCTION(CallInEditor, BlueprintCallable, Category = "RemoteControl")
    FString RC_GetActorInfo(const FString& Id);

    UFUNCTION(CallInEditor, BlueprintCallable, Category = "RemoteControl")
    bool RC_SetMaterialColor(const FString& Id, const FLinearColor& Color);

    UFUNCTION(CallInEditor, BlueprintCallable, Category = "RemoteControl")
    bool RC_SetMaterialTexture(const FString& Id, const FString& TexturePath);

    UFUNCTION(CallInEditor, BlueprintCallable, Category = "RemoteControl")
    bool RC_SetScale(const FString& Id, float SX, float SY, float SZ);

    UFUNCTION(CallInEditor, BlueprintCallable, Category = "RemoteControl")
    bool RC_SetLocation(const FString& Id, float X, float Y, float Z);

    UFUNCTION(CallInEditor, BlueprintCallable, Category = "RemoteControl")
    bool RC_Snapshot(const FString& Name, int32 ResX, int32 ResY, FString& OutPath);

    // ============================================================================
    // CAMERA CONTROL
    // ============================================================================

    UFUNCTION(CallInEditor, BlueprintCallable, Category = "RemoteControl|Camera")
    bool RC_SetCameraLocation(float X, float Y, float Z);

    UFUNCTION(CallInEditor, BlueprintCallable, Category = "RemoteControl|Camera")
    bool RC_MoveCameraRelative(float DeltaX, float DeltaY, float DeltaZ);

    UFUNCTION(CallInEditor, BlueprintCallable, Category = "RemoteControl|Camera")
    bool RC_SetCameraRotation(float Pitch, float Yaw, float Roll);

    // ============================================================================
    // COMPONENT QUERYING
    // ============================================================================

    UFUNCTION(CallInEditor, BlueprintCallable, Category = "RemoteControl|Components")
    bool RC_GetComponentInfo(const FString& Id, FString& OutJson);

    UFUNCTION(CallInEditor, BlueprintCallable, Category = "RemoteControl|Robot")
    bool RC_SetRobotJoints(const FString& Id, float J1, float J2, float J3, float J4,
                           float J5, float J6, float J7);

    UFUNCTION(CallInEditor, BlueprintCallable, Category = "RemoteControl|Robot")
    bool RC_SetRobotGripper(const FString& Id, float OpeningMeters);

    UFUNCTION(CallInEditor, BlueprintCallable, Category = "RemoteControl|Robot")
    FString RC_GetRobotState(const FString& Id);

    // Chaos task execution helpers.  Objects can be switched between a
    // kinematic planning state and a simulated rigid-body state, and a
    // temporary gripper attachment can be created/released through Remote
    // Control.  The success query uses world-space bounds, so it is also
    // usable by the Python recorder without duplicating UE geometry logic.
    UFUNCTION(CallInEditor, BlueprintCallable, Category = "RemoteControl|Physics")
    bool RC_SetPhysicsEnabled(const FString& Id, bool bEnabled);

    UFUNCTION(CallInEditor, BlueprintCallable, Category = "RemoteControl|Physics")
    bool RC_AttachToRobotGripper(const FString& ObjectId, const FString& RobotId, bool bAttach);

    UFUNCTION(CallInEditor, BlueprintCallable, Category = "RemoteControl|Physics")
    bool RC_CheckStackSuccess(const FString& MovingId, const FString& TargetId, float ToleranceCm,
                              FString& OutJson);

    // ============================================================================
    // PROPERTIES
    // ============================================================================

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Scene Builder|Materials")
    TSoftObjectPtr<UMaterial> BaseMaterial;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Scene Builder|Materials")
    bool bCreateMaterialInstances = true;

    UPROPERTY()
    TMap<FString, TWeakObjectPtr<AActor>> IdIndex;

protected:
    // ============================================================================
    // INTERNAL HELPERS
    // ============================================================================

    void ReindexExistingJsonActors();
    AActor* FindById(const FString& Id);

    UStaticMesh* LoadMeshFromPath(const FString& MeshPath);
    UClass* LoadBlueprintClass(const FString& ClassPath);
    UMaterialInstanceDynamic* CreateColoredMaterial(const FLinearColor& Color);

    UPROPERTY()
    TArray<AActor*> GeneratedActors;

    UPROPERTY()
    TMap<FString, UStaticMesh*> LoadedMeshes;

    UPROPERTY()
    TMap<FString, UMaterialInstanceDynamic*> ColorMaterials;

    UPROPERTY()
    TMap<FString, UPhysicsConstraintComponent*> ActiveGraspConstraints;
};
