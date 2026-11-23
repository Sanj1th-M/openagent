# AI Provider Flow and Fallback Architecture

```mermaid
graph TD
    Start["User Request / Audio Frame"] --> CheckMode{"Selected Engine Mode?"}

    CheckMode -->|Gemini Live API| TryLive["Try Gemini Live WebSocket"]
    TryLive -->|Success| ExecLive["Stream Audio & Function Calls"]
    TryLive -->|Network Error / Quota Exhaustion| FallbackPipeline["Fallback to Local/Modular Pipeline"]

    CheckMode -->|Modular Pipeline| ExecPipeline["Execute providers/engine_pipeline.py"]
    FallbackPipeline --> ExecPipeline

    subgraph ModularPipelineExec ["Modular Pipeline Execution Steps"]
        direction TB
        STTStep["1. Speech-to-Text: providers/stt_whisper.py"]
        LLMStep["2. LLM Provider: providers/llm_openai.py"]
        TTSStep["3. TTS Synthesis: Piper / ElevenLabs / System"]

        STTStep --> LLMStep
        LLMStep --> TTSStep
    end

    ExecPipeline --> ModularPipelineExec

    subgraph LLMLadder ["Core Gemini Fallback Ladder (core/gemini.py)"]
        direction TB
        LiveSession["1. Live Model (Ephemeral Session) - Quota Efficient"]
        PinnedREST["2. Pinned REST Model (e.g., gemini-2.5-flash)"]
        LatestREST["3. Rolling Latest REST (gemini-flash-latest)"]

        LiveSession -->|Quota / Failure| PinnedREST
        PinnedREST -->|Quota / Failure| LatestREST
    end

    subgraph ToolExecutionLoop ["Tool Execution & Loop Guard"]
        direction TB
        DetectCall["Model Requests Tool"]
        CheckRepeat{"Same tool called repeatedly?"}
        ExecuteOnce["Execute Tool Worker Thread"]
        CacheResult["Return Cached Prior Result"]
        FinalAnswer["Synthesize Voice Response"]

        DetectCall --> CheckRepeat
        CheckRepeat -->|No| ExecuteOnce
        CheckRepeat -->|Yes (Infinite Loop Guard)| CacheResult
        ExecuteOnce --> FinalAnswer
        CacheResult --> FinalAnswer
    end

    ModularPipelineExec --> ToolExecutionLoop
```
