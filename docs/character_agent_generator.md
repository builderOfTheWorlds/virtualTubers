Hey Hermes, I have this conceptual idea for a tuber management. I want to have a character generator and a character updater. 
The character generator would need to genrate the character information, knowlede, personality, and self image. 
The genrator would read through the source material and create a profile for the character based on what it interpereted from the source material.
Then the generator would use that profile to create a knowledge structure that we can represent in the knowledge graph that the character agent would have access to, this would be information about the characters backstory, what they know about the world, and themselves, their hopes, wishes, desires, and objectives. 
When a new character agent is launched would then have specific instructions to ingest the characters knowledge and act on it and become that character to act as a virtualTuber. 
The generator would also have instructions for the agent to create a 3d rendering of their avatar ( we are currently building this)
Character data should be preserved in postgres
We will add more features to this later, design it so we can easily add and modify more generation features

The character updater will be responsible for updating the character knowledge at every weekly reset,
Each week the timeline resets, and so must the characters memory, except for the most significant thing they added to their knowledge that week.
This will give them 


think of a knowledge graph, and ever week the edges between nodes are broken, 
The knowledge is still there but forgotton, like in obsidian there are {{name}} tags that can be used to reference information, but the character will not remember the connections between them until they are re-established through new experiences or interactions. 
The updater will also be responsible for adding new knowledge to the character's profile based on their experiences and interactions during the week. This could include new relationships, events, or discoveries that the character has made.
The updater will also need to handle any changes to the character's personality or self-image based on their experiences. For example, if a character has a significant event that changes their outlook on life, the updater should reflect that in their personality traits and self-image.
Additionally, the updater should be able to track the character's progress towards their goals and objectives, and update their knowledge graph accordingly. This could include adding new nodes for achievements or milestones, as well as updating existing nodes to reflect the character's growth and development.
Overall, the character generator and updater will work together to create a dynamic and evolving character that can adapt to new experiences and interactions, while also maintaining a consistent core identity. The generator will provide the initial foundation for the character, while the updater will ensure that the character continues to grow and evolve over time. This will allow for a rich and immersive virtualTuber experience, where characters can develop their own unique personalities, knowledge, and self-images based on their interactions with the world and other characters.


