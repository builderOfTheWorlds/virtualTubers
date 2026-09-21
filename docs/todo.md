ai automation tol to take political videos and add fart noises then have everyone look around akwardly 
it would download long segement politcs clips from news sources thab n parse them into the autoVideo applixcation



ai generate background music, teh beat and rythm is based on (or rather influences) the tone of the scene
if we need a scene to portray an emotion, a suitable music track shoudln be utilzed, 
Consider the quote, "sound effects makes it real, the music makes you feel"


WHen the 3layerGenerator generates story arcs we need to also generate some metric of an emotional state. 
Lets conssider for example some extreme edge cases, on the sad end if a player cahracter dies, the emotional state could be depressingly sad. 
On the other side, if the characters overcome evil and save the world the emotion would be joyous and exubrient. 
Not just joy and sorrow, but a wider gamut of emotions should be available for interperatation, 
We cana analyze historicalyl good music scores from movies (or check what research already exists) to udnerstand how the mood of the music can influcnce an audience, 
THenw we can take th is data and implement it for our content generatino engine (3layerGenerator),  this will allow us to produce a better stream by providign viewers with a more powerful feeling


the roundtable voice lines show appear as full brightness, then slowly fade to 50% after it completes its line and the next speaker starts 
(let it fade over time maybe 5 seconds, it should start to fade imediately after the voice ends).




WHen one of the tubers is speaking on the roundtable, the narrator Game Master should be reading all the narration, then the characters would be speaking their lines. If the tubers have a written intonatino like (low thoughtful tone), (whispering), (chuckling) and others should not be read out loud, they should instead influence how the line is read, can we do that with the current voice models? We also need to tie in the avatar emotes with these, we willneed a set of defined avatar actions that tie into the emotes. 




We need a way to represent the arcs and scenes with their ring composition in a graph so that a user can understand what story points represent waht part of the ring and can add, remove, and modify from a web gui













I'm considering pivoting from the current configuation where we genrate the campaign arcs, segments, and dialogue 




Hey Hermes, for virtual tubers, I want to explore the idea of having the tuber characters be different unique agents. COnsider this, at the moment (and i want you to chck this in the codebase) we have one agent generating the arcs, segmetns, and dialogue, and it knows all the information about the campaign, but thats not how a real game would be. In a real D&D game the gamemaster would know the entire world, players, and wahts happening. The palyers know their own charcters, and a little bit about the world, and each charcter has a slightly different knowledge about the world. What do you think of having one agent do the arcs and segments and the overall 'what happens' in the story, then when we generate the character dialogue, each chracter would have their own agent loaded into vram with its own context and communicate through the kafka bus. The GM would act as the narrator and direct what the charcters are doing and would direct the characters as messages through kafka, it would have to give scene direction to the character agents, and the chracter agents would have to discuss what they are doing and take action like a real game of D&D. The GM would have to railroad the characters through the story, adn the characters would have to follow with a 'yes, and' attitude. 
I also thin we might need a small change in the functionality for how the roundtable works. 
At the moment, as i understand it, the roundtableis a seperate entity to the regular tubers.
The way this should work is that each tuber (gm and characters) has their own agent and own tmux workspace, they all read and write to the same kafka bus to interact with eachother in real time. 
Their own stream is a representatino of each characters internal state, it would show the inner thoughts of the agent (ai thinking), then theey would have to decide on what to say back in the kafka bus. 
Then the roundtable would be a representation of all that spoken data, it would dispaly what the tubers say in kafka, it would be the table of players in a real D&D game, and their individual streams would be the inner thoughts and workings of the agents, kinda like a behinds the scenes look at whats happening. 





What if we increase the overall resolution of the streams, then reduced it to 1080 with ffmpeg, this would give us more room to display things. 






Hey Hermes, we need to make some changes to the tubers project, I want to updat the individual tubers tmux panel interface, 
At the moment we have rerun theater on the left side, the avatar and files in th emioddle, system and message bus on the right. 
I want to increase the overall resolution of the interface, then scale down to 1080p for the stream with ffmpeg, the higher resolution will allow us to fit more detail on the interface. 
we need to replace the left side with 2 panels, one radar chart(it will display some stats) i want this to look like a corner, can we do it with tmux?
Under that i want a knowledge graph of the characters knowledge
Then we will have the charcters avatar in the middle,
The thinking pane will show the models thinking process
On the right side we will show the kafka queue that all the tubers message in, it will need to show formatted data, not the raw kafka bus.
It should show in sequence all messages that are shown by the other tubers, without the kafka wrappers, it would show as "<mmDDyy HH:MM:SS> : <character_name>: <character message>"
THeres a narror pane to the left of kafka labelec 'c', this will act like a chat room icon, the first entry would be "all" and it would show all the kafka messages formatted, we will add more chats later

Check this below tet based representatino that I made of my idea
______________________________________
|radar  |       |          |c|        |
|chart  |       |          | |        |
|_______|       |  AVATAR  | |        |
|               |          | |        |
|               |__________| |        |
|               |          | | Kafka  |
|knoledge graph |          | |        |
|               | Thinking | |        |
|               |          | |        |
|               |          | |        |
|-------------------------------------|
|__________tmux bottom bar____________|








