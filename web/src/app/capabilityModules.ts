// Every capability's index, imported by name: Next has no import.meta.glob. A test
// (capabilityModules.test.ts) fails when a folder under src/capabilities has no line here.
import * as account from "../capabilities/account";
import * as afterServing from "../capabilities/after-serving";
import * as close from "../capabilities/close";
import * as pipeline from "../capabilities/pipeline";
import * as reader from "../capabilities/reader";
import * as report from "../capabilities/report";
import * as setup from "../capabilities/setup";
import * as today from "../capabilities/today";
import * as trial from "../capabilities/trial";
import * as understand from "../capabilities/understand";
import * as write from "../capabilities/write";

export const capabilityModules: Record<string, Record<string, unknown>> = {
  "account": account,
  "after-serving": afterServing,
  "close": close,
  "pipeline": pipeline,
  "reader": reader,
  "report": report,
  "setup": setup,
  "today": today,
  "trial": trial,
  "understand": understand,
  "write": write,
};
