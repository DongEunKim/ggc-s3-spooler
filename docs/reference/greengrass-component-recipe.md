# Greengrass Component Recipe — 참고 요약

> 출처: AWS Greengrass Developer Guide — Component recipe reference  
> 보존 목적: recipe.yaml 작성 시 참고한 공식 문서 핵심 내용

## 레시피 필수 필드

```yaml
RecipeFormatVersion: "2020-01-25"  # 현재 유일한 유효값
ComponentName: com.example.MyComponent
ComponentVersion: "1.0.0"          # Semantic Versioning
```

## ComponentDependencies

```yaml
ComponentDependencies:
  aws.greengrass.StreamManager:
    VersionRequirement: ">=2.1.0"
    DependencyType: HARD   # HARD(기본): 미충족 시 배포 실패
                           # SOFT: 미충족 시에도 배포 시도
```

## ComponentConfiguration

```yaml
ComponentConfiguration:
  DefaultConfiguration:
    key: value
```

- 배포 시 `--update-config`로 오버라이드 가능
- 레시피 내에서 `{configuration:/key}` 형식으로 참조

## Lifecycle

```yaml
Lifecycle:
  Install:
    RequiresPrivilege: false
    Script: pip install ...
  Run:
    Script: python3 -m mymodule
  Shutdown:
    Script: ...   # 선택사항
```

## Artifacts

```yaml
Artifacts:
  - URI: "s3://bucket/path/artifact.zip"
    Digest: "<SHA256 해시값>"
    Algorithm: SHA-256
    Unarchive: ZIP        # ZIP, NONE 중 선택
    Permission:
      Read: ALL           # OWNER, ALL
      Execute: NONE       # OWNER, ALL, NONE
```

## 컴포넌트 버전 관리

- 배포된 버전의 레시피는 변경 불가 → 변경 시 반드시 버전 올림
- 로컬 개발 시 `greengrass-cli deployment create --merge` 사용

## 참고 링크

- Recipe reference: https://docs.aws.amazon.com/greengrass/v2/developerguide/component-recipe-reference.html
- Local deployment: https://docs.aws.amazon.com/greengrass/v2/developerguide/test-components.html
