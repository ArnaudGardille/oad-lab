import { BaseAI } from "simulation/ai/common-api/baseAI.js";
import { Entity } from "simulation/ai/common-api/entity.js";
import { aiWarn } from "simulation/ai/common-api/utils.js";
import { Config } from "simulation/ai/forkbot/config.js";
import { Strategy } from "simulation/ai/forkbot/strategy.js";
import { Headquarters } from "simulation/ai/forkbot/headquarters.js";
import { Queue } from "simulation/ai/forkbot/queue.js";
import { QueueManager } from "simulation/ai/forkbot/queueManager.js";

export function PetraBot(settings)
{
	BaseAI.call(this, settings);

	this.playedTurn = 0;
	this.elapsedTime = 0;

	this.uniqueIDs = {
		"armies": 1,	// starts at 1 to allow easier tests on armies ID existence
		"bases": 1,	// base manager ID starts at one because "0" means "no base" on the map
		"plans": 0,	// training/building/research plans
		"transports": 1	// transport plans start at 1 because 0 might be used as none
	};

	this.Config = new Config(settings.difficulty, settings.behavior);

	// Banc d'essai : la personnalité est du BRUIT, pas une variable
	// d'intérêt. 0 A.D. 0.28 n'expose pas d'option de ligne de commande
	// pour la fixer, et le défaut de Petra (`behavior || "random"`)
	// tire personality.aggressive uniformément sur [0, 1] à CHAQUE
	// partie : les branches en `personality.aggressive >
	// personalityCut.strong` (0,7) ne s'exécutaient donc que dans ~30 %
	// des parties, au hasard — une stratégie portée par une de ces
	// branches voyait son effet dilué d'un facteur 3, et le « bot »
	// évalué était en fait un mélange aléatoire de personas
	// (diagnostic 2026-09-04). On épingle ici, APRÈS le constructeur et
	// AVANT setConfig (appelée depuis CustomInit) : la personnalité est
	// alors tirée dans la bande étroite de "balanced" ([0,37 ; 0,63]),
	// et la politique redevient une fonction du seul programme évolué.
	// Valeur miroir : oadlab.config.AI_BEHAVIOR (entre dans le
	// protocole d'éval).
	this.Config.behavior = "balanced";

	// Couche stratégie évoluable (oad-lab) : peut réécrire la Config
	// à chaque tour selon l'état du jeu. Voir strategy.js.
	this.strategy = new Strategy(this.Config);

	this.savedEvents = {};
}

PetraBot.prototype = Object.create(BaseAI.prototype);

PetraBot.prototype.CustomInit = function(gameState)
{
	if (this.isDeserialized)
	{
		// WARNING: the deserializations should not modify the metadatas infos inside their init functions
		this.canPlay = this.data.canPlay;
		this.turn = this.data.turn;
		this.playedTurn = this.data.playedTurn;
		this.elapsedTime = this.data.elapsedTime;
		this.savedEvents = this.data.savedEvents;
		for (const key in this.savedEvents)
		{
			for (const i in this.savedEvents[key])
			{
				if (!this.savedEvents[key][i].entityObj)
					continue;
				const evt = this.savedEvents[key][i];
				const evtmod = {};
				for (const keyevt in evt)
				{
					evtmod[keyevt] = evt[keyevt];
					evtmod.entityObj = new Entity(gameState.sharedScript, evt.entityObj);
					this.savedEvents[key][i] = evtmod;
				}
			}
		}

		this.Config.Deserialize(this.data.config);

		this.queueManager = new QueueManager(this.Config, {});
		this.queueManager.Deserialize(gameState, this.data.queueManager);
		this.queues = this.queueManager.queues;

		this.HQ = new Headquarters(this.Config);
		this.HQ.init(gameState, this.queues);
		this.HQ.Deserialize(gameState, this.data.HQ);

		this.uniqueIDs = this.data.uniqueIDs;
		this.isDeserialized = false;
		this.data = undefined;

		// initialisation needed after the completion of the deserialization
		this.HQ.postinit(gameState);
	}
	else
	{
		this.Config.setConfig(gameState);

		// this.queues can only be modified by the queue manager or things will go awry.
		this.queues = {};
		for (const i in this.Config.priorities)
			this.queues[i] = new Queue();

		this.queueManager = new QueueManager(this.Config, this.queues);

		this.HQ = new Headquarters(this.Config);

		this.HQ.init(gameState, this.queues);

		// Try to analyze our starting position and set a strategy.
		this.canPlay = this.HQ.gameAnalysis(gameState);
	}
};

PetraBot.prototype.OnUpdate = function(sharedScript)
{
	if (this.gameFinished || this.gameState.playerData.state == "defeated")
		return;

	for (const i in this.events)
	{
		if (i == "AIMetadata")   // not used inside petra
			continue;
		if (this.savedEvents[i] !== undefined)
			this.savedEvents[i] = this.savedEvents[i].concat(this.events[i]);
		else
			this.savedEvents[i] = this.events[i];
	}

	// Run the update every n turns, offset depending on player ID to balance the load
	this.elapsedTime = this.gameState.getTimeElapsed() / 1000;
	if (!this.playedTurn || (this.turn + this.player) % 8 == 5)
	{
		Engine.ProfileStart("PetraBot bot (player " + this.player +")");

		this.playedTurn++;

		if (!this.canPlay)
		{
			Engine.ProfileStop();
			return;
		}

		// Un crash de la stratégie ne coûte que l'ajustement de CE
		// tour : Petra continue en vanilla (repli sûr, oad-lab).
		try
		{
			this.strategy.update(this.gameState, this.Config);
		}
		catch (e)
		{
			if (!this.strategyWarned)
			{
				aiWarn("oadlab strategy.update failed: " + e);
				this.strategyWarned = true;
			}
		}

		this.HQ.update(this.gameState, this.queues, this.savedEvents);

		this.queueManager.update(this.gameState);

		for (const i in this.savedEvents)
			this.savedEvents[i] = [];

		Engine.ProfileStop();
	}

	this.turn++;
};

PetraBot.prototype.Serialize = function()
{
	const savedEvents = {};
	for (const key in this.savedEvents)
	{
		savedEvents[key] = this.savedEvents[key].slice();
		for (const i in savedEvents[key])
		{
			if (!savedEvents[key][i] || !savedEvents[key][i].entityObj)
				continue;
			const evt = savedEvents[key][i];
			const evtmod = {};
			for (const keyevt in evt)
				evtmod[keyevt] = evt[keyevt];
			evtmod.entityObj = evt.entityObj._entity;
			savedEvents[key][i] = evtmod;
		}
	}

	return {
		"canPlay": this.canPlay,
		"uniqueIDs": this.uniqueIDs,
		"turn": this.turn,
		"playedTurn": this.playedTurn,
		"elapsedTime": this.elapsedTime,
		"savedEvents": savedEvents,
		"config": this.Config.Serialize(),
		"queueManager": this.queueManager.Serialize(),
		"HQ": this.HQ.Serialize()
	};
};

PetraBot.prototype.Deserialize = function(data, sharedScript)
{
	this.isDeserialized = true;
	this.data = data;
};
