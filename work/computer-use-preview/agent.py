# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import os
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional, Any
from google import genai
from google.genai import types
import termcolor
from google.genai.types import (
    Part,
    GenerateContentConfig,
    Content,
    Candidate,
    FunctionResponse,
    FinishReason,
)
import time
from rich.console import Console
from rich.table import Table

from computers import EnvState, Computer

MAX_RECENT_TURN_WITH_SCREENSHOTS = 3
ROLLOUT_ADAPTER_VERSION = 1
LEGACY_COMPUTER_USE_MODELS = [
    "gemini-2.5-computer-use-preview-10-2025",
    "gemini-3-flash-preview",
    "gemini-3.1-pro-preview",
]

# Legacy predefined functions used by Gemini 2.5 and Gemini 3 preview models.
LEGACY_PREDEFINED_COMPUTER_USE_FUNCTIONS = [
    "open_web_browser",
    "click_at",
    "hover_at",
    "type_text_at",
    "scroll_document",
    "scroll_at",
    "wait_5_seconds",
    "go_back",
    "go_forward",
    "search",
    "navigate",
    "key_combination",
    "drag_and_drop",
]

# Predefined functions which are used in gemini-3.5-flash and future models.
PREDEFINED_COMPUTER_USE_FUNCTIONS = [
    "click",
    "double_click",
    "triple_click",
    "middle_click",
    "right_click",
    "mouse_down",
    "mouse_up",
    "move",
    "type",
    "drag_and_drop",
    "wait",
    "press_key",
    "key_down",
    "key_up",
    "hotkey",
    "take_screenshot",
    "scroll",
    "go_back",
    "navigate",
    "go_forward",
]

BLOCKED_LEGACY_FUNCTIONS = [
    "go_back",
    "go_forward",
    "search",
    "navigate",
]
BLOCKED_COMPUTER_USE_FUNCTIONS = ["navigate", "go_back", "go_forward"]
ALLOWED_COMPUTER_USE_FUNCTIONS = (
    set(PREDEFINED_COMPUTER_USE_FUNCTIONS + LEGACY_PREDEFINED_COMPUTER_USE_FUNCTIONS)
    - set(BLOCKED_LEGACY_FUNCTIONS)
    - set(BLOCKED_COMPUTER_USE_FUNCTIONS)
)


console = Console()

# Built-in Computer Use tools return an EnvState.
FunctionResponseT = EnvState


class BrowserAgent:
    def __init__(
        self,
        browser_computer: Computer,
        query: str,
        model_name: str,
        verbose: bool = True,
    ):
        self._browser_computer = browser_computer
        self._query = query
        self._model_name = model_name
        self._verbose = verbose
        self.final_reasoning = None
        self._client = genai.Client(
            api_key=os.environ.get("GEMINI_API_KEY"),
            vertexai=os.environ.get("USE_VERTEXAI", "0").lower() in ["true", "1"],
            project=os.environ.get("VERTEXAI_PROJECT"),
            location=os.environ.get("VERTEXAI_LOCATION"),
        )
        self._contents: list[Content] = [
            Content(
                role="user",
                parts=[
                    Part(text=self._query),
                ],
            )
        ]
        artifact_root = os.environ.get("ROLLOUT_ARTIFACT_DIR")
        self._trace_path = Path(artifact_root) / "trace.jsonl" if artifact_root else None
        self._turn_index = 0
        self._use_legacy_computer_use_function_call = (
            model_name in LEGACY_COMPUTER_USE_MODELS
        )

        self._generate_content_config = GenerateContentConfig(
            temperature=1,
            top_p=0.95,
            top_k=40,
            max_output_tokens=8192,
            tools=[
                types.Tool(
                    computer_use=types.ComputerUse(
                        environment=types.Environment.ENVIRONMENT_BROWSER,
                        # The browser is opened on the gym before the model
                        # starts. Keep the model inside that UI; it can use
                        # mouse, keyboard, scrolling, and screenshots.
                        excluded_predefined_functions=(
                            BLOCKED_LEGACY_FUNCTIONS
                            if self._use_legacy_computer_use_function_call
                            else BLOCKED_COMPUTER_USE_FUNCTIONS
                        ),
                    ),
                ),
            ],
            thinking_config=types.ThinkingConfig(include_thoughts=True),
        )

    def _write_trace(self, event: dict[str, Any]) -> None:
        if not self._trace_path:
            return
        self._trace_path.parent.mkdir(parents=True, exist_ok=True)
        event["timestamp"] = datetime.now(timezone.utc).isoformat()
        payload = json.dumps(event)
        redacted_values = os.environ.get("ROLLOUT_REDACT_VALUES", "").split("\0")
        for value in filter(None, redacted_values):
            payload = payload.replace(value, "[REDACTED]")
        with self._trace_path.open("a") as trace_file:
            trace_file.write(payload + "\n")

    def handle_action(
        self, action: types.FunctionCall, use_legacy_actions: bool
    ) -> FunctionResponseT:
        """Handles the action and returns the environment state."""
        if action.name not in ALLOWED_COMPUTER_USE_FUNCTIONS:
            raise PermissionError(
                f"Computer-use action {action.name!r} is outside the screenshot, "
                "mouse, and keyboard boundary"
            )
        if use_legacy_actions:
            return self.handle_legacy_action(action)

        if action.name == "open_web_browser":
            return self._browser_computer.open_web_browser()
        elif action.name == "click":
            x = self.denormalize_x(action.args["x"])
            y = self.denormalize_y(action.args["y"])
            return self._browser_computer.click_at(
                x=x,
                y=y,
            )
        elif action.name == "double_click":
            x = self.denormalize_x(action.args["x"])
            y = self.denormalize_y(action.args["y"])
            return self._browser_computer.double_click_at(
                x=x,
                y=y,
            )
        elif action.name == "triple_click":
            x = self.denormalize_x(action.args["x"])
            y = self.denormalize_y(action.args["y"])
            return self._browser_computer.triple_click_at(
                x=x,
                y=y,
            )
        elif action.name == "middle_click":
            x = self.denormalize_x(action.args["x"])
            y = self.denormalize_y(action.args["y"])
            return self._browser_computer.middle_click_at(
                x=x,
                y=y,
            )
        elif action.name == "right_click":
            x = self.denormalize_x(action.args["x"])
            y = self.denormalize_y(action.args["y"])
            return self._browser_computer.right_click_at(
                x=x,
                y=y,
            )
        elif action.name == "mouse_down":
            x = self.denormalize_x(action.args["x"])
            y = self.denormalize_y(action.args["y"])
            return self._browser_computer.mouse_down(
                x=x,
                y=y,
            )
        elif action.name == "mouse_up":
            x = self.denormalize_x(action.args["x"])
            y = self.denormalize_y(action.args["y"])
            return self._browser_computer.mouse_up(
                x=x,
                y=y,
            )
        elif action.name == "move":
            x = self.denormalize_x(action.args["x"])
            y = self.denormalize_y(action.args["y"])
            return self._browser_computer.hover_at(
                x=x,
                y=y,
            )
        elif action.name == "type":
            press_enter = action.args.get("press_enter", False)
            return self._browser_computer.type_text(
                text=action.args["text"],
                press_enter=press_enter,
            )
        elif action.name == "scroll":
            x = self.denormalize_x(action.args["x"])
            y = self.denormalize_y(action.args["y"])
            magnitude = action.args.get("magnitude", 800)
            direction = action.args["direction"]

            if direction in ("up", "down"):
                magnitude = self.denormalize_y(magnitude)
            elif direction in ("left", "right"):
                magnitude = self.denormalize_x(magnitude)
            else:
                raise ValueError("Unknown direction: ", direction)
            return self._browser_computer.scroll_at(
                x=x, y=y, direction=direction, magnitude=magnitude
            )
        elif action.name == "wait":
            wait_seconds = int(action.args.get("seconds", 1))
            return self._browser_computer.wait(wait_seconds)
        elif action.name == "go_back":
            return self._browser_computer.go_back()
        elif action.name == "go_forward":
            return self._browser_computer.go_forward()
        elif action.name == "navigate":
            return self._browser_computer.navigate(action.args["url"])
        elif action.name == "hotkey":
            return self._browser_computer.key_combination(action.args["keys"])
        elif action.name == "press_key":
            return self._browser_computer.press_key(action.args["key"])
        elif action.name == "key_down":
            return self._browser_computer.key_down(action.args["key"])
        elif action.name == "key_up":
            return self._browser_computer.key_up(action.args["key"])
        elif action.name == "take_screenshot":
            return self._browser_computer.take_screenshot()
        elif action.name == "drag_and_drop":
            x = self.denormalize_x(action.args["x"])
            y = self.denormalize_y(action.args["y"])
            destination_x = self.denormalize_x(action.args["destination_x"])
            destination_y = self.denormalize_y(action.args["destination_y"])
            return self._browser_computer.drag_and_drop(
                x=x,
                y=y,
                destination_x=destination_x,
                destination_y=destination_y,
            )
        else:
            raise ValueError(f"Unsupported function: {action}")

    def handle_legacy_action(self, action: types.FunctionCall) -> FunctionResponseT:
        """Handles the action defined in the legacy models, and returns the environment state."""
        if action.name == "open_web_browser":
            return self._browser_computer.open_web_browser()
        elif action.name == "click_at":
            x = self.denormalize_x(action.args["x"])
            y = self.denormalize_y(action.args["y"])
            return self._browser_computer.click_at(
                x=x,
                y=y,
            )
        elif action.name == "hover_at":
            x = self.denormalize_x(action.args["x"])
            y = self.denormalize_y(action.args["y"])
            return self._browser_computer.hover_at(
                x=x,
                y=y,
            )
        elif action.name == "type_text_at":
            x = self.denormalize_x(action.args["x"])
            y = self.denormalize_y(action.args["y"])
            press_enter = action.args.get("press_enter", False)
            clear_before_typing = action.args.get("clear_before_typing", True)
            return self._browser_computer.type_text_at(
                x=x,
                y=y,
                text=action.args["text"],
                press_enter=press_enter,
                clear_before_typing=clear_before_typing,
            )
        elif action.name == "scroll_document":
            return self._browser_computer.scroll_document(action.args["direction"])
        elif action.name == "scroll_at":
            x = self.denormalize_x(action.args["x"])
            y = self.denormalize_y(action.args["y"])
            magnitude = action.args.get("magnitude", 800)
            direction = action.args["direction"]

            if direction in ("up", "down"):
                magnitude = self.denormalize_y(magnitude)
            elif direction in ("left", "right"):
                magnitude = self.denormalize_x(magnitude)
            else:
                raise ValueError("Unknown direction: ", direction)
            return self._browser_computer.scroll_at(
                x=x, y=y, direction=direction, magnitude=magnitude
            )
        elif action.name == "wait_5_seconds":
            return self._browser_computer.wait_5_seconds()

        elif action.name == "go_back":
            return self._browser_computer.go_back()
        elif action.name == "go_forward":
            return self._browser_computer.go_forward()
        elif action.name == "search":
            return self._browser_computer.search()
        elif action.name == "navigate":
            return self._browser_computer.navigate(action.args["url"])
        elif action.name == "key_combination":
            return self._browser_computer.key_combination(
                action.args["keys"].split("+")
            )
        elif action.name == "drag_and_drop":
            x = self.denormalize_x(action.args["x"])
            y = self.denormalize_y(action.args["y"])
            destination_x = self.denormalize_x(action.args["destination_x"])
            destination_y = self.denormalize_y(action.args["destination_y"])
            return self._browser_computer.drag_and_drop(
                x=x,
                y=y,
                destination_x=destination_x,
                destination_y=destination_y,
            )
        else:
            raise ValueError(f"Unsupported function: {action}")

    def get_model_response(
        self, max_retries=5, base_delay_s=1
    ) -> types.GenerateContentResponse:
        for attempt in range(max_retries):
            try:
                response = self._client.models.generate_content(
                    model=self._model_name,
                    contents=self._contents,
                    config=self._generate_content_config,
                )
                return response  # Return response on success
            except Exception as e:
                print(e)
                if attempt < max_retries - 1:
                    delay = base_delay_s * (2**attempt)
                    message = (
                        f"Generating content failed on attempt {attempt + 1}. "
                        f"Retrying in {delay} seconds...\n"
                    )
                    termcolor.cprint(
                        message,
                        color="yellow",
                    )
                    time.sleep(delay)
                else:
                    termcolor.cprint(
                        f"Generating content failed after {max_retries} attempts.\n",
                        color="red",
                    )
                    raise

    def get_text(
        self, candidate: Candidate, thought: Optional[bool] = None
    ) -> Optional[str]:
        """Extract text, optionally selecting thought or final-answer parts."""
        if not candidate.content or not candidate.content.parts:
            return None
        text = []
        for part in candidate.content.parts:
            is_thought = bool(part.thought)
            if part.text and (thought is None or is_thought == thought):
                text.append(part.text)
        return " ".join(text) or None

    def extract_function_calls(self, candidate: Candidate) -> list[types.FunctionCall]:
        """Extracts the function call from the candidate."""
        if not candidate.content or not candidate.content.parts:
            return []
        ret = []
        for part in candidate.content.parts:
            if part.function_call:
                ret.append(part.function_call)
        return ret

    def run_one_iteration(self) -> Literal["COMPLETE", "CONTINUE"]:
        # Generate a response from the model.
        if self._verbose:
            with console.status(
                "Generating response from Gemini Computer Use...", spinner_style=None
            ):
                response = self.get_model_response()
        else:
            response = self.get_model_response()

        if not response.candidates:
            if (
                response.prompt_feedback
                and response.prompt_feedback.block_reason == types.BlockReason.SAFETY
            ):
                raise ValueError(
                    f"Response was blocked due to safety. Feedback: {response.prompt_feedback}"
                )
            print("Response has no candidates!")
            print(response)
            raise ValueError("Empty response")

        # Extract the text and function call from the response.
        candidate = response.candidates[0]
        # Append the model turn to conversation history.
        if candidate.content:
            self._contents.append(candidate.content)

        all_text = self.get_text(candidate)
        reasoning = self.get_text(candidate, thought=True) or all_text
        function_calls = self.extract_function_calls(candidate)

        # Retry the request in case of malformed FCs.
        if (
            not function_calls
            and not reasoning
            and candidate.finish_reason == FinishReason.MALFORMED_FUNCTION_CALL
        ):
            return "CONTINUE"

        if not function_calls:
            output = self.get_text(candidate, thought=False) or all_text
            final_thinking = self.get_text(candidate, thought=True)
            print(f"Agent Loop Complete: {output}")
            self.final_reasoning = output
            self._write_trace(
                {"type": "final", "output": output, "thinking": final_thinking}
            )
            return "COMPLETE"

        self._turn_index += 1
        turn_id = f"turn-{self._turn_index:03d}"
        trace_actions = [
            {
                "id": f"{turn_id}-action-{index:02d}",
                "name": call.name,
                "args": dict(call.args or {}),
            }
            for index, call in enumerate(function_calls, start=1)
        ]
        self._write_trace(
            {
                "type": "model_turn",
                "id": turn_id,
                "thinking": reasoning,
                "actions": trace_actions,
            }
        )

        function_call_strs = []
        for function_call in function_calls:
            # Print the function call and any reasoning.
            function_call_str = f"Name: {function_call.name}"
            if function_call.args:
                function_call_str += f"\nArgs:"
                for key, value in function_call.args.items():
                    function_call_str += f"\n  {key}: {value}"
            function_call_strs.append(function_call_str)

        table = Table(expand=True)
        table.add_column(
            "Gemini Computer Use Reasoning", header_style="magenta", ratio=1
        )
        table.add_column("Function Call(s)", header_style="cyan", ratio=1)
        table.add_row(reasoning, "\n".join(function_call_strs))
        if self._verbose:
            console.print(table)
            print()

        function_responses = []
        for action_index, function_call in enumerate(function_calls):
            extra_fr_fields = {}
            if function_call.args and (
                safety := function_call.args.get("safety_decision")
            ):
                decision = self._get_safety_confirmation(safety)
                if decision == "TERMINATE":
                    print("Terminating agent loop")
                    return "COMPLETE"
                # Explicitly mark the safety check as acknowledged.
                extra_fr_fields["safety_acknowledgement"] = "true"
            os.environ["ROLLOUT_CURRENT_ACTION_ID"] = trace_actions[action_index]["id"]
            try:
                if self._verbose:
                    with console.status(
                        "Sending command to Computer...", spinner_style=None
                    ):
                        fc_result = self.handle_action(
                            function_call, self._use_legacy_computer_use_function_call
                        )
                else:
                    fc_result = self.handle_action(
                        function_call, self._use_legacy_computer_use_function_call
                    )
            except Exception as exc:
                message = (
                    f"Computer action {function_call.name} failed: "
                    f"{type(exc).__name__}: {exc}. Inspect the current screen and try again."
                )
                self._write_trace(
                    {
                        "type": "action_error",
                        "id": trace_actions[action_index]["id"],
                        "message": message,
                    }
                )
                termcolor.cprint(message, "yellow")
                extra_fr_fields["error"] = message
                try:
                    fc_result = self._browser_computer.take_screenshot()
                except Exception as screenshot_exc:
                    raise RuntimeError(
                        "Computer action failed and the browser could not capture "
                        f"recovery state: {screenshot_exc}"
                    ) from exc
            finally:
                os.environ.pop("ROLLOUT_CURRENT_ACTION_ID", None)
            if isinstance(fc_result, EnvState):
                function_responses.append(
                    FunctionResponse(
                        name=function_call.name,
                        response={
                            "url": fc_result.url,
                            **extra_fr_fields,
                        },
                        parts=[
                            types.FunctionResponsePart(
                                inline_data=types.FunctionResponseBlob(
                                    mime_type="image/png", data=fc_result.screenshot
                                )
                            )
                        ],
                    )
                )

        self._contents.append(
            Content(
                role="user",
                parts=[Part(function_response=fr) for fr in function_responses],
            )
        )

        # only keep screenshots in the few most recent turns, remove the screenshot images from the old turns.
        turn_with_screenshots_found = 0
        for content in reversed(self._contents):
            if content.role == "user" and content.parts:
                # check if content has screenshot of the predefined computer use functions.
                has_screenshot = False
                for part in content.parts:
                    if (
                        part.function_response
                        and part.function_response.parts
                        and part.function_response.name
                        in (PREDEFINED_COMPUTER_USE_FUNCTIONS + LEGACY_PREDEFINED_COMPUTER_USE_FUNCTIONS)
                    ):
                        has_screenshot = True
                        break

                if has_screenshot:
                    turn_with_screenshots_found += 1
                    # remove the screenshot image if the number of screenshots exceed the limit.
                    if turn_with_screenshots_found > MAX_RECENT_TURN_WITH_SCREENSHOTS:
                        for part in content.parts:
                            if (
                                part.function_response
                                and part.function_response.parts
                                and part.function_response.name
                                in (PREDEFINED_COMPUTER_USE_FUNCTIONS + LEGACY_PREDEFINED_COMPUTER_USE_FUNCTIONS)
                            ):
                                part.function_response.parts = None

        return "CONTINUE"

    def _get_safety_confirmation(
        self, safety: dict[str, Any]
    ) -> Literal["CONTINUE", "TERMINATE"]:
        if safety["decision"] != "require_confirmation":
            raise ValueError(f"Unknown safety decision: safety['decision']")
        termcolor.cprint(
            "Safety service requires explicit confirmation!",
            color="yellow",
            attrs=["bold"],
        )
        print(safety["explanation"])
        explanation = safety["explanation"].lower()
        if (
            os.environ.get("ROLLOUT_AUTO_CONFIRM_DISCARD", "").lower() == "true"
            and "discard" in explanation
        ):
            self._write_trace(
                {
                    "type": "safety_confirmation",
                    "decision": "approved_local_discard",
                    "explanation": safety["explanation"],
                }
            )
            return "CONTINUE"
        if os.environ.get("ROLLOUT_NONINTERACTIVE", "").lower() == "true":
            self._write_trace(
                {
                    "type": "safety_confirmation",
                    "decision": "denied_noninteractive",
                    "explanation": safety["explanation"],
                }
            )
            return "TERMINATE"
        decision = ""
        while decision.lower() not in ("y", "n", "ye", "yes", "no"):
            decision = input("Do you wish to proceed? [Yes]/[No]\n")
        if decision.lower() in ("n", "no"):
            self._write_trace(
                {
                    "type": "safety_confirmation",
                    "decision": "denied",
                    "explanation": safety["explanation"],
                }
            )
            return "TERMINATE"
        self._write_trace(
            {
                "type": "safety_confirmation",
                "decision": "approved",
                "explanation": safety["explanation"],
            }
        )
        return "CONTINUE"

    def agent_loop(self):
        status = "CONTINUE"
        while status == "CONTINUE":
            status = self.run_one_iteration()

    def denormalize_x(self, x: int) -> int:
        return int(x / 1000 * self._browser_computer.screen_size()[0])

    def denormalize_y(self, y: int) -> int:
        return int(y / 1000 * self._browser_computer.screen_size()[1])
