#include "JsonSceneBuilder.h"
#include "Engine/Engine.h"
#include "Engine/World.h"
#include "Engine/StaticMeshActor.h"
#include "Components/StaticMeshComponent.h"
#include "PhysicsEngine/PhysicsConstraintComponent.h"
#include "PhysicsEngine/BodyInstance.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Materials/Material.h"
#include "Materials/MaterialInstance.h"
#include "UObject/ConstructorHelpers.h"
#include "HAL/PlatformFilemanager.h"
#include "Misc/FileHelper.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonReader.h"
#include "Dom/JsonObject.h"
#include "EngineUtils.h"
#include "Editor.h"
#include "Editor/EditorEngine.h"
#include "EditorViewportClient.h"
#include "LevelEditorViewport.h"
#include "Misc/Paths.h"
#include "HighResScreenshot.h"
// Asset registry for auto-detecting asset types
#include "AssetRegistry/AssetRegistryModule.h"
#include "Modules/ModuleManager.h"

AJsonSceneBuilder::AJsonSceneBuilder()
{
    PrimaryActorTick.bCanEverTick = false;

    static ConstructorHelpers::FObjectFinder<UMaterial> DefaultMaterial(TEXT("/Game/StarterContent/Materials/M_Basic_Wall"));
    if (DefaultMaterial.Succeeded())
    {
        BaseMaterial = DefaultMaterial.Object;
    }
}

void AJsonSceneBuilder::BeginPlay()
{
    Super::BeginPlay();
    ReindexExistingJsonActors();
    UE_LOG(LogTemp, Log, TEXT("JsonSceneBuilder: Ready for Remote Control commands"));
}

void AJsonSceneBuilder::EndPlay(const EEndPlayReason::Type EndPlayReason)
{
    Super::EndPlay(EndPlayReason);
}

// ============================================================================
// HELPER FUNCTIONS
// ============================================================================

UStaticMesh* AJsonSceneBuilder::LoadMeshFromPath(const FString& MeshPath)
{
	// Ensure object path includes object name (e.g., /Game/Path/Asset.Asset)
	auto NormalizeObjectPath = [](const FString& InPath) -> FString
	{
		if (InPath.Contains(TEXT("."))) return InPath;
		int32 SlashIndex;
		if (InPath.FindLastChar('/', SlashIndex))
		{
			const FString ObjectName = InPath.Mid(SlashIndex + 1);
			return InPath + TEXT(".") + ObjectName;
		}
		return InPath;
	};

	const FString ObjectPath = NormalizeObjectPath(MeshPath);
    if (UStaticMesh** CachedMesh = LoadedMeshes.Find(MeshPath))
    {
        return *CachedMesh;
    }

	UStaticMesh* Mesh = LoadObject<UStaticMesh>(nullptr, *ObjectPath);
    if (Mesh)
    {
		LoadedMeshes.Add(MeshPath, Mesh);
    }
    else
    {
		UE_LOG(LogTemp, Warning, TEXT("Failed to load mesh: %s"), *ObjectPath);
    }

    return Mesh;
}

UClass* AJsonSceneBuilder::LoadBlueprintClass(const FString& ClassOrObjectPath)
{
    // Convert an object path "/Game/Path/Asset.Asset" to a class path "/Game/Path/Asset.Asset_C"
    auto ToClassPath = [](const FString& InPath) -> FString
    {
        FString Out = InPath;
        // Ensure we have an object path first
        int32 SlashIndex;
        int32 DotIndex;
        FString PackagePart = InPath;
        FString ObjectName;
        if (!InPath.Contains(TEXT(".")))
        {
            if (InPath.FindLastChar('/', SlashIndex))
            {
                PackagePart = InPath;
                ObjectName = InPath.Mid(SlashIndex + 1);
                Out = InPath + TEXT(".") + ObjectName;
            }
            else
            {
                return InPath; // give up; invalid path
            }
        }
        else
        {
            if (InPath.FindLastChar('.', DotIndex))
            {
                PackagePart = InPath.Left(DotIndex);
                ObjectName = InPath.Mid(DotIndex + 1);
            }
        }

        if (ObjectName.EndsWith(TEXT("_C")))
        {
            return Out; // already a class path
        }
        return PackagePart + TEXT(".") + ObjectName + TEXT("_C");
    };

    const FString NormalizedClassPath = ToClassPath(ClassOrObjectPath);
    UClass* BlueprintClass = StaticLoadClass(AActor::StaticClass(), nullptr, *NormalizedClassPath);
    if (!BlueprintClass)
    {
        UE_LOG(LogTemp, Warning, TEXT("Failed to load Blueprint class: %s"), *NormalizedClassPath);
        return nullptr;
    }
    if (!BlueprintClass->IsChildOf(AActor::StaticClass()))
    {
        UE_LOG(LogTemp, Error, TEXT("Loaded class is not an AActor: %s"), *NormalizedClassPath);
        return nullptr;
    }
    return BlueprintClass;
}

UMaterialInstanceDynamic* AJsonSceneBuilder::CreateColoredMaterial(const FLinearColor& Color)
{
    FString ColorKey = FString::Printf(TEXT("R%.2f_G%.2f_B%.2f_A%.2f"),
                                     Color.R, Color.G, Color.B, Color.A);

    if (UMaterialInstanceDynamic** CachedMaterial = ColorMaterials.Find(ColorKey))
    {
        return *CachedMaterial;
    }

    if (!BaseMaterial.IsValid())
    {
        return nullptr;
    }

    UMaterialInstanceDynamic* DynamicMaterial = UMaterialInstanceDynamic::Create(
        BaseMaterial.LoadSynchronous(), this);

    if (DynamicMaterial)
    {
        DynamicMaterial->SetVectorParameterValue(FName("BaseColor"), Color);
        DynamicMaterial->SetVectorParameterValue(FName("Color"), Color);
        DynamicMaterial->SetVectorParameterValue(FName("Albedo"), Color);
        ColorMaterials.Add(ColorKey, DynamicMaterial);
    }

    return DynamicMaterial;
}

void AJsonSceneBuilder::ClearAllJsonGeneratedActors()
{
    UE_LOG(LogTemp, Log, TEXT("Clearing ALL JsonGenerated actors"));

    UWorld* World = GetWorld();
    if (!World) return;

    TArray<AActor*> ActorsToDestroy;

    for (TActorIterator<AActor> ActorItr(World); ActorItr; ++ActorItr)
    {
        AActor* Actor = *ActorItr;
        if (IsValid(Actor) && Actor->Tags.Contains(FName("JsonGenerated")))
        {
            ActorsToDestroy.Add(Actor);
        }
    }

    for (AActor* Actor : ActorsToDestroy)
    {
        Actor->Destroy();
    }

    GeneratedActors.Empty();
    IdIndex.Empty();
    LoadedMeshes.Empty();
    ColorMaterials.Empty();

    UE_LOG(LogTemp, Log, TEXT("Cleared %d JsonGenerated actors"), ActorsToDestroy.Num());
}

void AJsonSceneBuilder::ReindexExistingJsonActors()
{
    IdIndex.Empty();
    GeneratedActors.Empty();

    UWorld* World = GetWorld();
    if (!World) return;

    for (TActorIterator<AActor> It(World); It; ++It)
    {
        AActor* A = *It;
        if (!IsValid(A) || !A->Tags.Contains(FName("JsonGenerated"))) continue;

        FString FoundId;
        for (const FName& Tag : A->Tags)
        {
            const FString S = Tag.ToString();
            if (S.StartsWith(TEXT("RCID:")))
            {
                FoundId = S.Mid(5);
                break;
            }
        }
        if (FoundId.IsEmpty())
        {
            FoundId = A->GetActorLabel();
        }
        if (!FoundId.IsEmpty())
        {
            IdIndex.Add(FoundId, A);
            GeneratedActors.Add(A);
        }
    }
    UE_LOG(LogTemp, Log, TEXT("Reindexed %d actors"), IdIndex.Num());
}

bool AJsonSceneBuilder::RC_Reindex()
{
    ReindexExistingJsonActors();
    return true;
}

AActor* AJsonSceneBuilder::FindById(const FString& Id)
{
    if (TWeakObjectPtr<AActor>* Found = IdIndex.Find(Id))
    {
        if (Found->IsValid()) return Found->Get();
    }

    UWorld* World = GetWorld();
    if (!World) return nullptr;

    for (TActorIterator<AActor> It(World); It; ++It)
    {
        AActor* A = *It;
        if (!IsValid(A)) continue;

        for (const FName& Tag : A->Tags)
        {
            if (Tag.ToString() == ("RCID:" + Id))
            {
                IdIndex.Add(Id, A);
                return A;
            }
        }
        if (A->GetActorLabel() == Id)
        {
            IdIndex.Add(Id, A);
            return A;
        }
    }
    return nullptr;
}

// ============================================================================
// PRIMARY RC FUNCTIONS
// ============================================================================

bool AJsonSceneBuilder::RC_AddAsset(const FString& Id, const FString& AssetPath, float X, float Y, float Z,
                                     float ScaleX, float ScaleY, float ScaleZ,
                                     float Pitch, float Yaw, float Roll,
                                     const FLinearColor& Color)
{
    UWorld* World = GetWorld();
    if (!World) return false;

	FString ResolvedAssetPath = AssetRegistry::Resolve(AssetPath);

    // The Panda is a kinematic actor made from the official link meshes.  It
    // is handled before normal mesh/Blueprint detection because the registry
    // entry is a semantic robot asset rather than a UE object path.
    if (ResolvedAssetPath.Equals(TEXT("franka_panda"), ESearchCase::IgnoreCase) ||
        AssetPath.Equals(TEXT("panda"), ESearchCase::IgnoreCase) ||
        AssetPath.Equals(TEXT("franka"), ESearchCase::IgnoreCase))
    {
        if (AActor* Existing = FindById(Id))
        {
            IdIndex.Remove(Id);
            GeneratedActors.Remove(Existing);
            Existing->Destroy();
        }
        FActorSpawnParameters RobotParams;
        RobotParams.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
        AFrankaPandaRobot* Robot = World->SpawnActor<AFrankaPandaRobot>(
            AFrankaPandaRobot::StaticClass(), FVector(X, Y, Z), FRotator(Pitch, Yaw, Roll), RobotParams);
        if (!Robot) return false;
        Robot->SetActorScale3D(FVector(ScaleX, ScaleY, ScaleZ));
        Robot->SetActorLabel(Id);
        Robot->Tags.AddUnique(FName(TEXT("JsonGenerated")));
        Robot->Tags.AddUnique(FName(TEXT("Robot")));
        Robot->Tags.AddUnique(FName(TEXT("RCID:") + Id));
        IdIndex.Add(Id, Robot);
        GeneratedActors.Add(Robot);
        return true;
    }

    // Sawyer and UR5e are native UE5 articulated link assets.  They do not
    // depend on PORTAL-Trace at runtime; the imported meshes are addressed by
    // the UE5 robot actor below.
    FString UE5RobotModel;
    if (ResolvedAssetPath.Contains(TEXT("/Robots/PORTALTrace/Sawyer"), ESearchCase::IgnoreCase) ||
        AssetPath.Equals(TEXT("sawyer"), ESearchCase::IgnoreCase))
    {
        UE5RobotModel = TEXT("Sawyer");
    }
    else if (ResolvedAssetPath.Contains(TEXT("/Robots/PORTALTrace/UR5e"), ESearchCase::IgnoreCase) ||
             AssetPath.Equals(TEXT("ur5e"), ESearchCase::IgnoreCase) ||
             AssetPath.Equals(TEXT("ur5"), ESearchCase::IgnoreCase))
    {
        UE5RobotModel = TEXT("UR5e");
    }
    if (!UE5RobotModel.IsEmpty())
    {
        if (AActor* Existing = FindById(Id))
        {
            IdIndex.Remove(Id);
            GeneratedActors.Remove(Existing);
            Existing->Destroy();
        }
        FActorSpawnParameters RobotParams;
        RobotParams.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
        AUE5ArticulatedRobot* Robot = World->SpawnActor<AUE5ArticulatedRobot>(
            AUE5ArticulatedRobot::StaticClass(), FVector(X, Y, Z), FRotator(Pitch, Yaw, Roll), RobotParams);
        if (!Robot || !Robot->SetRobotModel(UE5RobotModel)) return false;
        Robot->SetActorScale3D(FVector(ScaleX, ScaleY, ScaleZ));
        Robot->SetActorLabel(Id);
        Robot->Tags.AddUnique(FName(TEXT("JsonGenerated")));
        Robot->Tags.AddUnique(FName(TEXT("Robot")));
        Robot->Tags.AddUnique(FName(TEXT("RCID:") + Id));
        IdIndex.Add(Id, Robot);
        GeneratedActors.Add(Robot);
        return true;
    }

	// Normalize to object path for asset registry queries ("/Game/Path/Asset.Asset")
	auto ToObjectPath = [](const FString& InPath) -> FString
	{
		if (InPath.Contains(TEXT("."))) return InPath;
		int32 SlashIndex;
		if (InPath.FindLastChar('/', SlashIndex))
		{
			const FString ObjectName = InPath.Mid(SlashIndex + 1);
			return InPath + TEXT(".") + ObjectName;
		}
		return InPath;
	};

	const FString ObjectPath = ToObjectPath(ResolvedAssetPath);

    // Auto-detect asset type using AssetRegistry if available
    bool bIsBlueprint = false;
	{
		FAssetRegistryModule& AssetRegistryModule = FModuleManager::LoadModuleChecked<FAssetRegistryModule>("AssetRegistry");
		IAssetRegistry& AR = AssetRegistryModule.Get();
		FAssetData AssetData = AR.GetAssetByObjectPath(FSoftObjectPath(ObjectPath));
        if (AssetData.IsValid())
        {
            const FString ClassName = AssetData.AssetClassPath.GetAssetName().ToString();
            // The asset data for a Blueprint asset uses class "Blueprint"
            // Static meshes will be "StaticMesh"
            bIsBlueprint = ClassName.Equals(TEXT("Blueprint"), ESearchCase::IgnoreCase);
        }
		else
		{
			// Fallback: rely on _C suffix if present
			bIsBlueprint = ResolvedAssetPath.Contains(TEXT("_C")) || ResolvedAssetPath.EndsWith(TEXT("_C"));
		}
	}

    // Remove existing actor with this ID
    if (AActor* Existing = FindById(Id))
    {
        IdIndex.Remove(Id);
        GeneratedActors.Remove(Existing);
        Existing->Destroy();
    }

	AActor* NewActor = nullptr;

    FActorSpawnParameters SpawnParams;
    SpawnParams.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
    // DO NOT SET NAME - Let Unreal auto-generate to avoid crashes

    if (bIsBlueprint)
    {
        // Convert asset object path to class path (append _C on object)
        UClass* BlueprintClass = LoadBlueprintClass(ObjectPath);
        if (!BlueprintClass) return false;

        NewActor = World->SpawnActor<AActor>(BlueprintClass, FVector(X, Y, Z),
                                             FRotator(Pitch, Yaw, Roll), SpawnParams);
    }
	else
    {
		UStaticMesh* Mesh = LoadMeshFromPath(ObjectPath);
        if (!Mesh) return false;

        AStaticMeshActor* MeshActor = World->SpawnActor<AStaticMeshActor>(
            AStaticMeshActor::StaticClass(), FVector(X, Y, Z),
            FRotator(Pitch, Yaw, Roll), SpawnParams);

        if (MeshActor)
        {
            UStaticMeshComponent* MeshComp = MeshActor->GetStaticMeshComponent();
            if (MeshComp)
            {
                MeshComp->SetMobility(EComponentMobility::Movable);
                MeshComp->SetStaticMesh(Mesh);

                if (Color != FLinearColor::White && bCreateMaterialInstances && BaseMaterial.IsValid())
                {
                    if (UMaterialInstanceDynamic* DynMat = CreateColoredMaterial(Color))
                    {
                        MeshComp->SetMaterial(0, DynMat);
                    }
                }
            }
            NewActor = MeshActor;
        }
    }

    if (!NewActor) return false;

    NewActor->SetActorScale3D(FVector(ScaleX, ScaleY, ScaleZ));
    NewActor->SetActorLabel(Id);
    NewActor->Tags.AddUnique(FName(TEXT("JsonGenerated")));
    NewActor->Tags.AddUnique(FName(TEXT("RCID:") + Id));

    IdIndex.Add(Id, NewActor);
    GeneratedActors.Add(NewActor);

    UE_LOG(LogTemp, Log, TEXT("RC_AddAsset: %s -> SUCCESS"), *Id);
    return true;
}

bool AJsonSceneBuilder::RC_Remove(const FString& Id)
{
    if (AActor* A = FindById(Id))
    {
        A->Destroy();
        IdIndex.Remove(Id);
        return true;
    }
    return false;
}

FString AJsonSceneBuilder::RC_GetActorInfo(const FString& Id)
{
    if (AActor* Actor = FindById(Id))
    {
        FVector Location = Actor->GetActorLocation();
        FRotator Rotation = Actor->GetActorRotation();
        FVector Scale = Actor->GetActorScale3D();

        // Build a JSON-like string with actor information
        FString Result = FString::Printf(
            TEXT("{\"id\":\"%s\",\"name\":\"%s\",\"class\":\"%s\",\"location\":{\"x\":%.2f,\"y\":%.2f,\"z\":%.2f},\"rotation\":{\"pitch\":%.2f,\"yaw\":%.2f,\"roll\":%.2f},\"scale\":{\"x\":%.2f,\"y\":%.2f,\"z\":%.2f}}"),
            *Id,
            *Actor->GetName(),
            *Actor->GetClass()->GetName(),
            Location.X, Location.Y, Location.Z,
            Rotation.Pitch, Rotation.Yaw, Rotation.Roll,
            Scale.X, Scale.Y, Scale.Z
        );

        return Result;
    }

    return TEXT("{\"error\":\"Actor not found\"}");
}

bool AJsonSceneBuilder::RC_SetMaterialColor(const FString& Id, const FLinearColor& Color)
{
    if (AActor* A = FindById(Id))
    {
        if (UStaticMeshComponent* SMC = Cast<UStaticMeshComponent>(
            A->GetComponentByClass(UStaticMeshComponent::StaticClass())))
        {
            if (bCreateMaterialInstances && BaseMaterial.IsValid())
            {
                if (UMaterialInstanceDynamic* Dyn = CreateColoredMaterial(Color))
                {
                    SMC->SetMaterial(0, Dyn);
                    return true;
                }
            }
        }
    }
    return false;
}

bool AJsonSceneBuilder::RC_SetMaterialTexture(const FString& Id, const FString& TexturePath)
{
    // Find the actor
    AActor* A = FindById(Id);
    if (!A)
    {
        UE_LOG(LogTemp, Error, TEXT("RC_SetMaterialTexture: Actor '%s' not found"), *Id);
        return false;
    }

    // Get the static mesh component
    UStaticMeshComponent* SMC = Cast<UStaticMeshComponent>(
        A->GetComponentByClass(UStaticMeshComponent::StaticClass()));
    if (!SMC)
    {
        UE_LOG(LogTemp, Error, TEXT("RC_SetMaterialTexture: Actor '%s' has no StaticMeshComponent"), *Id);
        return false;
    }

    // FIRST: Try to load as a Material (complete material asset)
    // Material paths in Unreal should be: /Game/Path/MaterialName (without duplicate .MaterialName)
    // But registry might have: /Game/Path/MaterialName.MaterialName
    // Normalize the path by removing the duplicate suffix
    FString MaterialPath = TexturePath;

    // Check if path has duplicate name (e.g., /Game/Path/Material.Material)
    int32 LastDot = MaterialPath.Find(TEXT("."), ESearchCase::CaseSensitive, ESearchDir::FromEnd);
    if (LastDot > 0)
    {
        FString BeforeDot = MaterialPath.Left(LastDot);
        FString AfterDot = MaterialPath.Mid(LastDot + 1);
        FString BaseName = FPaths::GetBaseFilename(BeforeDot);

        // If the part after the dot matches the base name, it's a duplicate - remove it
        if (AfterDot == BaseName)
        {
            MaterialPath = BeforeDot;
            UE_LOG(LogTemp, Log, TEXT("RC_SetMaterialTexture: Normalized material path: %s -> %s"), *TexturePath, *MaterialPath);
        }
    }

    // Try loading as material with normalized path first
    UMaterialInterface* Material = Cast<UMaterialInterface>(
        StaticLoadObject(UMaterialInterface::StaticClass(), nullptr, *MaterialPath));

    // If that failed, try the original path (in case it's already correct)
    if (!Material && MaterialPath != TexturePath)
    {
        Material = Cast<UMaterialInterface>(
            StaticLoadObject(UMaterialInterface::StaticClass(), nullptr, *TexturePath));
    }

    if (Material)
    {
        // It's a material! Apply it directly
        SMC->SetMaterial(0, Material);
        SMC->MarkRenderStateDirty();
        UE_LOG(LogTemp, Log, TEXT("RC_SetMaterialTexture: Applied material '%s' directly to '%s'"),
            *Material->GetName(), *Id);
        return true;
    }

    // SECOND: Try to load as a Texture (for texture parameter approach)
    UTexture2D* Texture = Cast<UTexture2D>(
        StaticLoadObject(UTexture2D::StaticClass(), nullptr, *TexturePath));
    if (!Texture)
    {
        UE_LOG(LogTemp, Error, TEXT("RC_SetMaterialTexture: Neither material nor texture found at path: %s"), *TexturePath);
        UE_LOG(LogTemp, Error, TEXT("  Tried material path: %s"), *MaterialPath);
        return false;
    }

    // Check if material instances are enabled
    if (!bCreateMaterialInstances || !BaseMaterial.IsValid())
    {
        UE_LOG(LogTemp, Error, TEXT("RC_SetMaterialTexture: Material instances disabled or BaseMaterial invalid"));
        return false;
    }

    // Load the base material
    UMaterial* BaseMat = BaseMaterial.Get();
    if (!BaseMat)
    {
        UE_LOG(LogTemp, Error, TEXT("RC_SetMaterialTexture: Failed to load BaseMaterial"));
        return false;
    }

    // Create dynamic material instance
    UMaterialInstanceDynamic* DynamicMat = UMaterialInstanceDynamic::Create(BaseMat, this);
    if (!DynamicMat)
    {
        UE_LOG(LogTemp, Error, TEXT("RC_SetMaterialTexture: Failed to create dynamic material instance"));
        return false;
    }

    // Get texture parameters from the base material
    TArray<FMaterialParameterInfo> TextureParams;
    TArray<FGuid> TextureGuids;
    BaseMat->GetAllTextureParameterInfo(TextureParams, TextureGuids);

    // Try to set texture using material's existing parameters
    bool bTextureSet = false;
    if (TextureParams.Num() > 0)
    {
        // Use the first texture parameter found
        DynamicMat->SetTextureParameterValue(TextureParams[0].Name, Texture);
        UE_LOG(LogTemp, Log, TEXT("RC_SetMaterialTexture: Texture set via parameter '%s'"),
               *TextureParams[0].Name.ToString());
        bTextureSet = true;
    }
    else
    {
        // Try common texture parameter names as fallback
        TArray<FName> CommonNames = {
            FName("Texture"),
            FName("BaseTexture"),
            FName("Diffuse"),
            FName("Albedo"),
            FName("BaseColor")
        };

        for (const FName& ParamName : CommonNames)
        {
            DynamicMat->SetTextureParameterValue(ParamName, Texture);
            bTextureSet = true;
            UE_LOG(LogTemp, Log, TEXT("RC_SetMaterialTexture: Texture set via common parameter '%s'"),
                   *ParamName.ToString());
            break; // Use first one
        }
    }

    if (!bTextureSet)
    {
        UE_LOG(LogTemp, Error, TEXT("RC_SetMaterialTexture: No suitable texture parameters found in BaseMaterial"));
        return false;
    }

    // Apply the material to the mesh component
    SMC->SetMaterial(0, DynamicMat);
    SMC->MarkRenderStateDirty();

    UE_LOG(LogTemp, Log, TEXT("RC_SetMaterialTexture: Successfully applied texture to '%s'"), *Id);
    return true;
}

bool AJsonSceneBuilder::RC_SetScale(const FString& Id, float SX, float SY, float SZ)
{
    if (AActor* A = FindById(Id))
    {
        A->SetActorScale3D(FVector(SX, SY, SZ));
        return true;
    }
    return false;
}

bool AJsonSceneBuilder::RC_SetLocation(const FString& Id, float X, float Y, float Z)
{
    if (AActor* A = FindById(Id))
    {
        if (UStaticMeshComponent* SMC = Cast<UStaticMeshComponent>(
            A->GetComponentByClass(UStaticMeshComponent::StaticClass())))
        {
            SMC->SetMobility(EComponentMobility::Movable);
        }
        A->SetActorLocation(FVector(X, Y, Z), false, nullptr, ETeleportType::TeleportPhysics);
        return true;
    }
    return false;
}

bool AJsonSceneBuilder::RC_Snapshot(const FString& Name, int32 ResX, int32 ResY, FString& OutPath)
{
    OutPath.Empty();
#if WITH_EDITOR
    if (!GEditor || ResX <= 0 || ResY <= 0 || ResX > 4096 || ResY > 4096)
        return false;
    // Select an actual level viewport, independent of OS foreground focus.
    FViewport* CaptureViewport = nullptr;
    for (FLevelEditorViewportClient* Client : GEditor->GetLevelViewportClients())
    {
        if (Client && Client->Viewport && Client->IsPerspective())
        {
            CaptureViewport = Client->Viewport;
            break;
        }
    }
    if (!CaptureViewport) return false;
    const FString Dir = FPaths::ConvertRelativePathToFull(FPaths::ProjectSavedDir() / TEXT("AIShots"));
    IFileManager::Get().MakeDirectory(*Dir, true);
    OutPath = Dir / (FPaths::GetCleanFilename(Name) + TEXT(".png"));
    FHighResScreenshotConfig& Config = GetHighResScreenshotConfig();
    Config.SetResolution(ResX, ResY, 1.0f);
    Config.SetFilename(OutPath);
    if (!CaptureViewport->TakeHighResScreenShot()) return false;
    // Remote Control runs on the game thread. Explicit draw processes the
    // capture even when Slate suppresses redraws for hidden/minimized windows.
    CaptureViewport->Draw(false);
    return IFileManager::Get().FileSize(*OutPath) > 0;
#else
    return false;
#endif
}

// ============================================================================
// CAMERA CONTROL
// ============================================================================

bool AJsonSceneBuilder::RC_SetCameraLocation(float X, float Y, float Z)
{
#if WITH_EDITOR
    if (GEditor && GEditor->GetActiveViewport())
    {
        FEditorViewportClient* ViewportClient = (FEditorViewportClient*)GEditor->GetActiveViewport()->GetClient();
        if (ViewportClient)
        {
            ViewportClient->SetViewLocation(FVector(X, Y, Z));
            ViewportClient->Invalidate();
            return true;
        }
    }
#endif
    return false;
}

bool AJsonSceneBuilder::RC_MoveCameraRelative(float DeltaX, float DeltaY, float DeltaZ)
{
#if WITH_EDITOR
    if (GEditor && GEditor->GetActiveViewport())
    {
        FEditorViewportClient* ViewportClient = (FEditorViewportClient*)GEditor->GetActiveViewport()->GetClient();
        if (ViewportClient)
        {
            FVector CurrentLocation = ViewportClient->GetViewLocation();
            ViewportClient->SetViewLocation(CurrentLocation + FVector(DeltaX, DeltaY, DeltaZ));
            ViewportClient->Invalidate();
            return true;
        }
    }
#endif
    return false;
}

bool AJsonSceneBuilder::RC_SetCameraRotation(float Pitch, float Yaw, float Roll)
{
#if WITH_EDITOR
    if (GEditor && GEditor->GetActiveViewport())
    {
        FEditorViewportClient* ViewportClient = (FEditorViewportClient*)GEditor->GetActiveViewport()->GetClient();
        if (ViewportClient)
        {
            ViewportClient->SetViewRotation(FRotator(Pitch, Yaw, Roll));
            ViewportClient->Invalidate();
            return true;
        }
    }
#endif
    return false;
}

bool AJsonSceneBuilder::RC_GetComponentInfo(const FString& Id, FString& OutJson)
{
    if (AActor* Actor = FindById(Id))
    {
        TSharedPtr<FJsonObject> RootObject = MakeShareable(new FJsonObject);
        TArray<TSharedPtr<FJsonValue>> ComponentsArray;

        // Get all components
        TArray<UActorComponent*> AllComponents;
        Actor->GetComponents(AllComponents);

        for (UActorComponent* Component : AllComponents)
        {
            if (!IsValid(Component)) continue;

            TSharedPtr<FJsonObject> ComponentObj = MakeShareable(new FJsonObject);
            ComponentObj->SetStringField(TEXT("name"), Component->GetName());
            ComponentObj->SetStringField(TEXT("class"), Component->GetClass()->GetName());

            // Get component location and bounds if it's a scene component
            if (USceneComponent* SceneComp = Cast<USceneComponent>(Component))
            {
                FVector Location = SceneComp->GetComponentLocation();
                FVector Scale = SceneComp->GetComponentScale();
                FRotator Rotation = SceneComp->GetComponentRotation();

                TSharedPtr<FJsonObject> TransformObj = MakeShareable(new FJsonObject);
                TransformObj->SetNumberField(TEXT("x"), Location.X);
                TransformObj->SetNumberField(TEXT("y"), Location.Y);
                TransformObj->SetNumberField(TEXT("z"), Location.Z);
                ComponentObj->SetObjectField(TEXT("location"), TransformObj);

                TSharedPtr<FJsonObject> ScaleObj = MakeShareable(new FJsonObject);
                ScaleObj->SetNumberField(TEXT("x"), Scale.X);
                ScaleObj->SetNumberField(TEXT("y"), Scale.Y);
                ScaleObj->SetNumberField(TEXT("z"), Scale.Z);
                ComponentObj->SetObjectField(TEXT("scale"), ScaleObj);

                TSharedPtr<FJsonObject> RotationObj = MakeShareable(new FJsonObject);
                RotationObj->SetNumberField(TEXT("pitch"), Rotation.Pitch);
                RotationObj->SetNumberField(TEXT("yaw"), Rotation.Yaw);
                RotationObj->SetNumberField(TEXT("roll"), Rotation.Roll);
                ComponentObj->SetObjectField(TEXT("rotation"), RotationObj);

                // Get bounding box information
                FBox Bounds = SceneComp->Bounds.GetBox();
                TSharedPtr<FJsonObject> BoundsObj = MakeShareable(new FJsonObject);
                BoundsObj->SetNumberField(TEXT("min_x"), Bounds.Min.X);
                BoundsObj->SetNumberField(TEXT("min_y"), Bounds.Min.Y);
                BoundsObj->SetNumberField(TEXT("min_z"), Bounds.Min.Z);
                BoundsObj->SetNumberField(TEXT("max_x"), Bounds.Max.X);
                BoundsObj->SetNumberField(TEXT("max_y"), Bounds.Max.Y);
                BoundsObj->SetNumberField(TEXT("max_z"), Bounds.Max.Z);
                ComponentObj->SetObjectField(TEXT("bounds"), BoundsObj);

                // Check for specific component types that indicate surfaces or floors
                FString ComponentName = Component->GetName().ToLower();
                bool bIsSurface = ComponentName.Contains(TEXT("surface"));
                bool bIsFloor = ComponentName.Contains(TEXT("floor"));

                if (bIsSurface || bIsFloor)
                {
                    ComponentObj->SetBoolField(TEXT("is_surface"), bIsSurface);
                    ComponentObj->SetBoolField(TEXT("is_floor"), bIsFloor);

                    // Calculate surface/floor center and dimensions for placement
                    FVector Center = Bounds.GetCenter();
                    FVector Size = Bounds.GetSize();

                    TSharedPtr<FJsonObject> CenterObj = MakeShareable(new FJsonObject);
                    CenterObj->SetNumberField(TEXT("x"), Center.X);
                    CenterObj->SetNumberField(TEXT("y"), Center.Y);
                    CenterObj->SetNumberField(TEXT("z"), Center.Z);
                    ComponentObj->SetObjectField(TEXT("center"), CenterObj);

                    TSharedPtr<FJsonObject> SizeObj = MakeShareable(new FJsonObject);
                    SizeObj->SetNumberField(TEXT("width"), Size.X);
                    SizeObj->SetNumberField(TEXT("height"), Size.Y);
                    SizeObj->SetNumberField(TEXT("depth"), Size.Z);
                    ComponentObj->SetObjectField(TEXT("size"), SizeObj);

                    // For surfaces, calculate placement height (top of surface + offset)
                    if (bIsSurface)
                    {
                        ComponentObj->SetNumberField(TEXT("placement_height"), Bounds.Max.Z + 5.0);
                    }

                    // For floors, calculate placement height (top of floor + offset)
                    if (bIsFloor)
                    {
                        ComponentObj->SetNumberField(TEXT("placement_height"), Bounds.Max.Z + 1.0);
                    }

                    // Set component type for easy identification
                    ComponentObj->SetStringField(TEXT("component_type"), bIsSurface ? TEXT("surface") : TEXT("floor"));
                }
            }

            ComponentsArray.Add(MakeShareable(new FJsonValueObject(ComponentObj)));
        }

        RootObject->SetArrayField(TEXT("components"), ComponentsArray);

        // Serialize to JSON string
        TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&OutJson);
        FJsonSerializer::Serialize(RootObject.ToSharedRef(), Writer);

        return true;
    }

    return false;
}

bool AJsonSceneBuilder::RC_SetRobotJoints(const FString& Id, float J1, float J2, float J3, float J4,
                                          float J5, float J6, float J7)
{
    if (AFrankaPandaRobot* Robot = Cast<AFrankaPandaRobot>(FindById(Id)))
    {
        return Robot->SetJointPositions7(J1, J2, J3, J4, J5, J6, J7);
    }
    if (AUE5ArticulatedRobot* Robot = Cast<AUE5ArticulatedRobot>(FindById(Id)))
    {
        return Robot->SetJointPositions7(J1, J2, J3, J4, J5, J6, J7);
    }
    return false;
}

bool AJsonSceneBuilder::RC_SetRobotGripper(const FString& Id, float OpeningMeters)
{
    if (AFrankaPandaRobot* Robot = Cast<AFrankaPandaRobot>(FindById(Id)))
    {
        return Robot->SetGripper(OpeningMeters);
    }
    if (AUE5ArticulatedRobot* Robot = Cast<AUE5ArticulatedRobot>(FindById(Id)))
    {
        return Robot->SetGripper(OpeningMeters);
    }
    return false;
}

FString AJsonSceneBuilder::RC_GetRobotState(const FString& Id)
{
    if (AFrankaPandaRobot* Robot = Cast<AFrankaPandaRobot>(FindById(Id)))
    {
        const TArray<float> Joints = Robot->GetJointPositions();
        FString Json = TEXT("{\"id\":\"") + Id + TEXT("\",\"joint_positions\":[");
        for (int32 Index = 0; Index < Joints.Num(); ++Index)
        {
            if (Index > 0) Json += TEXT(",");
            Json += FString::SanitizeFloat(Joints[Index]);
        }
        Json += TEXT("],\"gripper_opening_m\":") + FString::SanitizeFloat(Robot->GripperOpeningMeters);
        Json += TEXT(",\"tcp_world_cm\":[") + FString::SanitizeFloat(Robot->GetToolCenterPoint().X);
        Json += TEXT(",") + FString::SanitizeFloat(Robot->GetToolCenterPoint().Y);
        Json += TEXT(",") + FString::SanitizeFloat(Robot->GetToolCenterPoint().Z) + TEXT("]}");
        return Json;
    }
    if (AUE5ArticulatedRobot* Robot = Cast<AUE5ArticulatedRobot>(FindById(Id)))
    {
        const TArray<float> Joints = Robot->GetJointPositions();
        FString Json = TEXT("{\"id\":\"") + Id + TEXT("\",\"robot_model\":\"") + Robot->RobotModel + TEXT("\",\"dof\":") + FString::FromInt(Robot->GetDOF()) + TEXT(",\"joint_positions\":[");
        for (int32 Index = 0; Index < Joints.Num(); ++Index)
        {
            if (Index > 0) Json += TEXT(",");
            Json += FString::SanitizeFloat(Joints[Index]);
        }
        Json += TEXT("],\"gripper_opening_m\":") + FString::SanitizeFloat(Robot->GripperOpeningMeters);
        Json += TEXT(",\"tcp_world_cm\":[") + FString::SanitizeFloat(Robot->GetToolCenterPoint().X);
        Json += TEXT(",") + FString::SanitizeFloat(Robot->GetToolCenterPoint().Y);
        Json += TEXT(",") + FString::SanitizeFloat(Robot->GetToolCenterPoint().Z) + TEXT("]}");
        return Json;
    }
    return TEXT("{}");
}

bool AJsonSceneBuilder::RC_SetPhysicsEnabled(const FString& Id, bool bEnabled)
{
    AActor* Actor = FindById(Id);
    if (!Actor) return false;
    TArray<UPrimitiveComponent*> PrimitiveComponents;
    Actor->GetComponents<UPrimitiveComponent>(PrimitiveComponents);
    bool bChanged = false;
    for (UPrimitiveComponent* Primitive : PrimitiveComponents)
    {
        if (!Primitive) continue;
        Primitive->SetMobility(EComponentMobility::Movable);
        Primitive->SetCollisionEnabled(ECollisionEnabled::QueryAndPhysics);
        Primitive->SetSimulatePhysics(bEnabled);
        Primitive->SetEnableGravity(bEnabled);
        if (bEnabled) Primitive->WakeAllRigidBodies();
        bChanged = true;
    }
    return bChanged;
}

bool AJsonSceneBuilder::RC_AttachToRobotGripper(const FString& ObjectId, const FString& RobotId, bool bAttach)
{
    AActor* Object = FindById(ObjectId);
    AActor* Robot = FindById(RobotId);
    if (!Object || !Robot || !Robot->GetRootComponent()) return false;
    UPrimitiveComponent* ObjectPrimitive = Cast<UPrimitiveComponent>(
        Object->GetComponentByClass(UPrimitiveComponent::StaticClass()));
    UPrimitiveComponent* ToolPrimitive = nullptr;
    TArray<UPrimitiveComponent*> RobotPrimitives;
    Robot->GetComponents<UPrimitiveComponent>(RobotPrimitives);
    for (UPrimitiveComponent* Primitive : RobotPrimitives)
    {
        if (Primitive && Primitive->GetName().Equals(TEXT("Tool"), ESearchCase::IgnoreCase))
        {
            ToolPrimitive = Primitive;
            break;
        }
    }
    if (!ToolPrimitive && RobotPrimitives.Num() > 0) ToolPrimitive = RobotPrimitives.Last();
    if (!ObjectPrimitive || !ToolPrimitive) return false;
    if (bAttach)
    {
        if (UPhysicsConstraintComponent** Existing = ActiveGraspConstraints.Find(ObjectId))
        {
            if (IsValid(*Existing)) (*Existing)->DestroyComponent();
            ActiveGraspConstraints.Remove(ObjectId);
        }
        ObjectPrimitive->DetachFromComponent(FDetachmentTransformRules::KeepWorldTransform);
        ObjectPrimitive->SetMobility(EComponentMobility::Movable);
        ObjectPrimitive->SetCollisionEnabled(ECollisionEnabled::QueryAndPhysics);
        ObjectPrimitive->SetSimulatePhysics(true);
        ObjectPrimitive->SetEnableGravity(false);
        UPhysicsConstraintComponent* Constraint = NewObject<UPhysicsConstraintComponent>(this,
            *FString::Printf(TEXT("GraspConstraint_%s"), *ObjectId));
        if (!Constraint) return false;
        Constraint->RegisterComponent();
        Constraint->SetDisableCollision(true);
        Constraint->SetLinearXLimit(ELinearConstraintMotion::LCM_Locked, 0.0f);
        Constraint->SetLinearYLimit(ELinearConstraintMotion::LCM_Locked, 0.0f);
        Constraint->SetLinearZLimit(ELinearConstraintMotion::LCM_Locked, 0.0f);
        Constraint->SetAngularSwing1Limit(EAngularConstraintMotion::ACM_Locked, 0.0f);
        Constraint->SetAngularSwing2Limit(EAngularConstraintMotion::ACM_Locked, 0.0f);
        Constraint->SetAngularTwistLimit(EAngularConstraintMotion::ACM_Locked, 0.0f);
        Constraint->SetConstrainedComponents(ObjectPrimitive, NAME_None, ToolPrimitive, NAME_None);
        ActiveGraspConstraints.Add(ObjectId, Constraint);
        Object->Tags.AddUnique(FName(TEXT("RC:Grasped")));
        return true;
    }
    if (UPhysicsConstraintComponent** Existing = ActiveGraspConstraints.Find(ObjectId))
    {
        if (IsValid(*Existing)) (*Existing)->BreakConstraint();
        if (IsValid(*Existing)) (*Existing)->DestroyComponent();
        ActiveGraspConstraints.Remove(ObjectId);
    }
    Object->Tags.Remove(FName(TEXT("RC:Grasped")));
    ObjectPrimitive->SetEnableGravity(true);
    ObjectPrimitive->SetSimulatePhysics(true);
    ObjectPrimitive->WakeAllRigidBodies();
    return true;
}

bool AJsonSceneBuilder::RC_CheckStackSuccess(const FString& MovingId, const FString& TargetId,
                                             float ToleranceCm, FString& OutJson)
{
    AActor* Moving = FindById(MovingId);
    AActor* Target = FindById(TargetId);
    if (!Moving || !Target) return false;
    UPrimitiveComponent* MovingPrimitive = Cast<UPrimitiveComponent>(
        Moving->GetComponentByClass(UPrimitiveComponent::StaticClass()));
    UPrimitiveComponent* TargetPrimitive = Cast<UPrimitiveComponent>(
        Target->GetComponentByClass(UPrimitiveComponent::StaticClass()));
    if (!MovingPrimitive || !TargetPrimitive) return false;
    const FBox MovingBounds = MovingPrimitive->Bounds.GetBox();
    const FBox TargetBounds = TargetPrimitive->Bounds.GetBox();
    const FVector MovingCenter = MovingBounds.GetCenter();
    const FVector TargetCenter = TargetBounds.GetCenter();
    const float HorizontalError = FVector2D(MovingCenter.X - TargetCenter.X,
                                            MovingCenter.Y - TargetCenter.Y).Size();
    const float ExpectedZ = TargetBounds.Max.Z + MovingBounds.GetExtent().Z;
    const float VerticalError = FMath::Abs(MovingCenter.Z - ExpectedZ);
    const float Tol = FMath::Max(0.1f, ToleranceCm);
    const bool bSuccess = HorizontalError <= Tol && VerticalError <= Tol;
    OutJson = FString::Printf(TEXT("{\"success\":%s,\"horizontal_error_cm\":%.5f,\"vertical_error_cm\":%.5f,\"tolerance_cm\":%.5f}"),
                               bSuccess ? TEXT("true") : TEXT("false"), HorizontalError, VerticalError, Tol);
    return true;
}

// ============================================================================
// ASSET REGISTRY
// ============================================================================

void AssetRegistry::Initialize()
{
    if (bInitialized) return;

    const FString RegistryPath = FPaths::ProjectDir() / TEXT("Config/asset_registry.json");

    FString JsonString;
    if (!FFileHelper::LoadFileToString(JsonString, *RegistryPath))
    {
        UE_LOG(LogTemp, Error, TEXT("[AssetRegistry] Failed to load: %s"), *RegistryPath);
        bInitialized = true;
        return;
    }

    TSharedPtr<FJsonObject> RootObject;
    TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(JsonString);

    if (!FJsonSerializer::Deserialize(Reader, RootObject) || !RootObject.IsValid())
    {
        UE_LOG(LogTemp, Error, TEXT("[AssetRegistry] Failed to parse JSON"));
        bInitialized = true;
        return;
    }

    const TSharedPtr<FJsonObject>* CategoriesObject;
    if (RootObject->TryGetObjectField(TEXT("categories"), CategoriesObject))
    {
        for (const auto& Category : (*CategoriesObject)->Values)
        {
            const TSharedPtr<FJsonObject>* CategoryObject;
            if (Category.Value->TryGetObject(CategoryObject))
            {
                for (const auto& Asset : (*CategoryObject)->Values)
                {
                    FString AssetPath;
                    if (Asset.Value->TryGetString(AssetPath))
                    {
                        AssetMap.Add(Asset.Key.ToLower(), AssetPath);
                    }
                }
            }
        }
    }

    const TSharedPtr<FJsonObject>* AliasesObject;
    if (RootObject->TryGetObjectField(TEXT("aliases"), AliasesObject))
    {
        for (const auto& Alias : (*AliasesObject)->Values)
        {
            FString TargetName;
            if (Alias.Value->TryGetString(TargetName))
            {
                if (FString* TargetPath = AssetMap.Find(TargetName.ToLower()))
                {
                    AssetMap.Add(Alias.Key.ToLower(), *TargetPath);
                }
            }
        }
    }

    bInitialized = true;
    UE_LOG(LogTemp, Log, TEXT("[AssetRegistry] Loaded %d assets"), AssetMap.Num());
}

FString AssetRegistry::Resolve(const FString& NameOrPath)
{
    if (!bInitialized) Initialize();

    if (NameOrPath.StartsWith(TEXT("/Game/")) ||
        NameOrPath.StartsWith(TEXT("/Script/")) ||
        NameOrPath.StartsWith(TEXT("/Engine/")))
    {
        return NameOrPath;
    }

    if (FString* FoundPath = AssetMap.Find(NameOrPath.ToLower()))
    {
        return *FoundPath;
    }

    return NameOrPath;
}
