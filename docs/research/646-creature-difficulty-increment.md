# #646 — creatureDifficultyIncrementPerDOMDifficulty + UpdateCreaturesBaseDifficulty (Rohdaten)

**Issue:** #646 (Spike, reine Datenextraktion) · **Quelle:** `/srv/rbgame/packs/00_win_data.zip`
(Build 2.0.58485, byte-identisch GOG == Dedi) · **Stand:** 2026-09-17

> Reine Rohdaten — keine Formel, keine Richtwert-Tabelle, keine Interpretation.
> Die Weiterverarbeitung (absolute Richtwert-Kurve, `213-wave-richtwert.md` §3/§7)
> gehört in #205 bzw. das Follow-up von #646.

---

## 1 · `creatureDifficultyIncrementPerDOMDifficulty` (default)

`lua/missions/survival/v2/dom_survival_jungle_rules_default.lua:106–156` — wortwörtlich:

```lua
rules.creatureDifficultyIncrementPerDOMDifficulty =
{
	[1] =
	{
		0,	 -- initial difficulty
		0, -- difficulty level 2
		0, -- difficulty level 3
		0, -- difficulty level 4
		0, -- difficulty level 5
		0.5, -- difficulty level 6
		0.5, -- difficulty level 7
		0.5, -- difficulty level 8
		1.5, -- difficulty level 9
	},
	[2] =
	{
		0, -- initial difficulty
		2, -- difficulty level 2
		0, -- difficulty level 3
		1, -- difficulty level 4
		0, -- difficulty level 5
		1, -- difficulty level 6
		0, -- difficulty level 7
		1, -- difficulty level 8
		0, -- difficulty level 9
	},
	[3] =
	{
		2,	 -- initial difficulty
		0, -- difficulty level 2
		1, -- difficulty level 3
		0, -- difficulty level 4
		1, -- difficulty level 5
		0, -- difficulty level 6
		1, -- difficulty level 7
		0, -- difficulty level 8
		1, -- difficulty level 9
	},
	[4] =
	{
		2,	 -- initial difficulty
		1, -- difficulty level 2
		0, -- difficulty level 3
		1, -- difficulty level 4
		0, -- difficulty level 5
		1, -- difficulty level 6
		0, -- difficulty level 7
		1, -- difficulty level 8
		1, -- difficulty level 9
	},
}
```

---

## 2 · `_normal.lua` überschreibt den Schlüssel **nicht**

`lua/missions/survival/v2/dom_survival_jungle_rules_normal.lua` (188 Zeilen) enthält
`creatureDifficultyIncrementPerDOMDifficulty` **nicht**. Die Datei überschreibt nur:

- `prepareSpawnTime`
- `maxAttackCountPerDifficulty`
- `multiplayerWaves`
- `extraWaves`

⇒ Für `creatureDifficultyIncrementPerDOMDifficulty` gilt `_normal` == `_default`
(erbt die Tabelle aus §1). Die im Issue offene Frage
(`riftbreaker_server_difficulty: coop_normal` → `default` oder `_normal`) ist für
diesen Schlüssel damit irrelevant: beide zeigen auf denselben Wert.

---

## 3 · Funktionskörper (`dom_manager.lua`, v2)

`lua/missions/v2/dom_manager.lua:1178–1216` — wortwörtlich:

```lua
function dom_mananger:IncreaseCreaturesBaseDifficulty()
	self:VerboseLog("IncreaseCreaturesBaseDifficulty" )

	if ( self.rules.creatureDifficultyIncrementPerDOMDifficulty ~= nil ) then

		local playersCounter = self:GetPlayersCounter()
		local index = Clamp( playersCounter, 1, #self.rules.creatureDifficultyIncrementPerDOMDifficulty )

		CampaignService:IncreaseCreaturesBaseDifficulty( self.rules.creatureDifficultyIncrementPerDOMDifficulty[index][self.currentDifficultyLevel] )
	end
end

function dom_mananger:RevertCreaturesBaseDifficulty()
	self:VerboseLog( "RevertCreaturesBaseDifficulty" )

	if ( self.rules.creatureDifficultyIncrementPerDOMDifficulty ~= nil ) then

		local playersCounter = self:GetPlayersCounter()
		local index = Clamp( playersCounter, 1, #self.rules.creatureDifficultyIncrementPerDOMDifficulty )

		for i = 1, self.currentDifficultyLevel do
			CampaignService:DecreaseCreaturesBaseDifficulty( self.rules.creatureDifficultyIncrementPerDOMDifficulty[index][i] )
		end
	end
end

function dom_mananger:UpdateCreaturesBaseDifficulty()
	self:VerboseLog( "UpdateCreaturesBaseDifficulty" )

	if ( self.rules.creatureDifficultyIncrementPerDOMDifficulty ~= nil ) then

		local playersCounter = self:GetPlayersCounter()
		local index = Clamp( playersCounter, 1, #self.rules.creatureDifficultyIncrementPerDOMDifficulty )

		for i = 1, self.currentDifficultyLevel do
			CampaignService:IncreaseCreaturesBaseDifficulty( self.rules.creatureDifficultyIncrementPerDOMDifficulty[index][i] )
		end
	end
end
```

---

## 4 · Offen (gehört **nicht** hierher)

- Wirkung von `CampaignService:IncreaseCreaturesBaseDifficulty(...)` (C++, nicht in Lua sichtbar).
- Interpretation (kumulative Semantik, Solo-Index `[1]`, HP-Effekt) → #205 bzw. Follow-up.
