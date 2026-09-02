import { BaseAI } from "simulation/ai/common-api/baseAI.js";

export function NullBot(settings)
{
	BaseAI.call(this, settings);
}

NullBot.prototype = Object.create(BaseAI.prototype);
